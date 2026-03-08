from django.db.models import Count, Max, Q
from django.template.loader import render_to_string
from django.views.generic import ListView, DetailView, TemplateView
from django.utils import timezone
from django.http import JsonResponse, FileResponse, HttpResponse
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from datetime import timedelta
import io
import os
import json
from google import genai
from google.genai import types
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

from bills.models import Bill, BillVote


class HomeView(TemplateView):
    template_name = 'core/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Only show Ongoing (Active) bills on Home
        context['latest_bills'] = Bill.objects.active_bills().order_by('-created_at')[:3]

        # Trending Discussions only for currently viewable bills (Active or Closed < 30 days)
        visibility_threshold = timezone.now().date() - timedelta(days=30)
        context['trending_discussions'] = Bill.objects.filter(
            Q(status=Bill.Status.ACTIVE) |
            Q(status=Bill.Status.CLOSED, closing_date__gte=visibility_threshold),
            messages__isnull=False
        ).annotate(
            last_message=Max('messages__created_at')
        ).order_by('-last_message')[:3]

        return context


class BillListView(ListView):
    model = Bill
    template_name = 'core/bill_list.html'
    context_object_name = 'ongoing_bills'
    paginate_by = 20

    def get_queryset(self):
        filter_type = self.request.GET.get('filter', 'ongoing')
        search_query = self.request.GET.get('q', '').strip()

        today = timezone.now().date()
        threshold = today - timedelta(days=30)

        qs = Bill.objects.filter(is_processed_by_ai=True)

        if search_query:
            qs = qs.filter(
                Q(title__icontains=search_query) |
                Q(ai_analysis__english__short_summary__icontains=search_query)
            )

        if filter_type == 'ongoing':
            qs = qs.filter(status=Bill.Status.ACTIVE).filter(
                Q(closing_date__gte=today) | Q(closing_date__isnull=True)
            )
        elif filter_type == 'closed':
            qs = qs.filter(status=Bill.Status.CLOSED, closing_date__gte=threshold)
        elif filter_type == 'all':
            qs = qs.filter(
                Q(status=Bill.Status.ACTIVE) |
                Q(status=Bill.Status.CLOSED, closing_date__gte=threshold)
            )

        return qs.order_by('-created_at')

    def get(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            # We bypass pagination for search/filter results to ensure everything shows
            queryset = self.get_queryset()
            data = []
            for b in queryset:
                data.append({
                    'id': str(b.id),
                    'title': b.title,
                    'created_at': b.created_at.strftime("%b %d, %Y"),
                    'status': b.current_status,
                    'summary': b.ai_analysis.get('english', {}).get('short_summary', ''),
                    'support_count': b.support_count,
                    'oppose_count': b.oppose_count,
                    'view_count': b.view_count,
                    'detail_url': f"/bills/{b.id}/"
                })
            return JsonResponse({'bills': data})
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['hot_bills'] = Bill.objects.filter(
            status=Bill.Status.ACTIVE,
            view_count__gte=1000,
            is_processed_by_ai=True
        ).order_by('-view_count')[:3]
        return context


class BillDetailView(DetailView):
    model = Bill
    template_name = 'core/bill_detail.html'
    context_object_name = 'bill'

    def get_object(self):
        obj = super().get_object()
        if obj.status == Bill.Status.CLOSED and obj.closing_date:
            visibility_threshold = timezone.now().date() - timedelta(days=30)
            if obj.closing_date < visibility_threshold:
                from django.http import Http404
                raise Http404("This bill is no longer available for public viewing.")

        session_key = f'viewed_bill_{obj.id}'
        last_viewed = self.request.session.get(session_key)
        now = timezone.now().timestamp()

        if not last_viewed or (now - last_viewed) > 7200:
            obj.view_count += 1
            obj.save()
            self.request.session[session_key] = now
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bill = self.get_object()
        context['is_closed'] = bill.current_status == Bill.Status.CLOSED

        if bill.closing_date:
            context['closing_date_iso'] = bill.closing_date.isoformat()

        user_vote = None
        if self.request.user.is_authenticated:
            user_vote = BillVote.objects.filter(bill=bill, user=self.request.user).first()
        context['user_vote'] = user_vote
        context['preferred_lang'] = self.request.user.profile.language if self.request.user.is_authenticated else 'en'
        return context

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'status': 'error', 'message': 'Not authenticated'}, status=403)

        bill = self.get_object()
        if bill.current_status == Bill.Status.CLOSED:
            return JsonResponse({'status': 'error', 'message': 'Public participation for this bill has ended.'},
                                status=403)

        vote_type = request.POST.get('vote_type')
        reason = request.POST.get('reason', '')

        if vote_type in ['support', 'oppose']:
            vote, created = BillVote.objects.get_or_create(
                bill=bill,
                user=request.user,
                defaults={'vote_type': vote_type, 'reason': reason}
            )

            if not created:
                if vote.vote_type != vote_type:
                    if vote.vote_type == 'support':
                        bill.support_count -= 1
                        bill.oppose_count += 1
                    else:
                        bill.oppose_count -= 1
                        bill.support_count += 1
                vote.vote_type = vote_type
                vote.reason = reason
                vote.save()
                bill.save()

            return JsonResponse({
                'status': 'success',
                'vote_type': vote.vote_type,
                'reason': vote.reason,
                'support_count': bill.support_count,
                'oppose_count': bill.oppose_count
            })

        return JsonResponse({'status': 'error', 'message': 'Invalid vote type'}, status=400)


class DiscussionListView(ListView):
    model = Bill
    template_name = 'core/discussion_list.html'
    context_object_name = 'discussions'
    paginate_by = 12

    def get_queryset(self):
        filter_type = self.request.GET.get('filter', 'ongoing')
        today = timezone.now().date()
        threshold = today - timedelta(days=30)
        base_qs = Bill.objects.annotate(
            msg_count=Count('messages'),
            last_activity=Max('messages__created_at')
        ).filter(msg_count__gt=0)
        if filter_type == 'ongoing':
            queryset = base_qs.filter(status=Bill.Status.ACTIVE).filter(
                Q(closing_date__gte=today) | Q(closing_date__isnull=True)
            )
        elif filter_type == 'closed':
            queryset = base_qs.filter(status=Bill.Status.CLOSED, closing_date__gte=threshold)
        else:
            queryset = base_qs.filter(
                Q(status=Bill.Status.ACTIVE) |
                Q(status=Bill.Status.CLOSED, closing_date__gte=threshold)
            )
        query = self.request.GET.get('q')
        if query:
            queryset = queryset.filter(Q(title__icontains=query))
        return queryset.order_by('-last_activity')

    def get(self, request, *args, **kwargs):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            queryset = self.get_queryset()
            data = [{'id': b.id, 'title': b.title, 'msg_count': b.msg_count, 'view_count': b.view_count, 'status': b.current_status, 'summary': b.ai_analysis.get('english', {}).get('short_summary', ''), 'chat_url': f"/bills/{b.id}/chat/"} for b in queryset]
            return JsonResponse({'discussions': data})
        return super().get(request, *args, **kwargs)


class AboutView(TemplateView): template_name = 'core/about.html'


class PrivacyView(TemplateView): template_name = 'core/privacy.html'


class TermsView(TemplateView): template_name = 'core/terms.html'


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing.")
    return genai.Client(api_key=api_key)


@login_required
def generate_bill_pdf(request, bill_id):
    # PDF generation allowed for Active AND Closed (<30 days) bills
    bill = get_object_or_404(Bill, id=bill_id)

    visibility_threshold = timezone.now().date() - timedelta(days=30)
    if bill.status == Bill.Status.CLOSED and bill.closing_date < visibility_threshold:
        return JsonResponse({'error': 'Report is no longer available.'}, status=410)

    votes = BillVote.objects.filter(bill=bill).exclude(reason__isnull=True).exclude(reason__exact='')

    if not votes.exists():
        return JsonResponse({'error': 'Insufficient participation data to generate a meaningful report.'}, status=400)

    try:
        client = get_gemini_client()
        perspective_data = "\n".join([f"- {v.reason}" for v in votes[:40]])

        config = types.GenerateContentConfig(
            system_instruction="You are a Kenyan policy analyst. Summarize public sentiment. Return JSON: 'executive_summary', 'top_concerns' (list), 'overall_sentiment'.",
            response_mime_type="application/json",
            temperature=0.3
        )

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"Analyze perspectives for: {bill.title}\n\nData:\n{perspective_data}",
            config=config
        )
        analysis = json.loads(response.text)
    except Exception:
        return JsonResponse({'error': 'PDF generation failed.'}, status=503)

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    p.saveState()
    p.setFont("Helvetica-Bold", 60)
    p.setFillAlpha(0.05)
    p.translate(width / 2, height / 2)
    p.rotate(45)
    p.drawCentredString(0, 0, "VOICED.")
    p.restoreState()

    p.setFont("Helvetica-Bold", 24)
    p.drawString(50, height - 80, "LEGISLATIVE REPORT")
    p.setStrokeColor(colors.red)
    p.setLineWidth(2)
    p.line(50, height - 90, 150, height - 90)

    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, height - 130, f"BILL: {bill.title.upper()}")
    p.setFont("Helvetica", 10)
    p.setFillColor(colors.grey)
    p.drawString(50, height - 150,
                 f"Total Support: {bill.support_count} | Total Oppose: {bill.oppose_count} | Sentiment: {analysis.get('overall_sentiment', 'N/A')}")

    p.setFillColor(colors.black)
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, height - 190, "EXECUTIVE SUMMARY")

    p.setFont("Helvetica", 11)
    text_object = p.beginText(50, height - 210)
    text_object.setLeading(14)

    summary = analysis.get('executive_summary', '')
    for line in summary.split('.'):
        if line.strip():
            text_object.textLine(line.strip() + ".")

    text_object.moveCursor(0, 20)
    text_object.setFont("Helvetica-Bold", 11)
    text_object.textLine("PRIMARY CITIZEN CONCERNS:")
    text_object.setFont("Helvetica", 11)

    for concern in analysis.get('top_concerns', []):
        text_object.textLine(f"- {concern}")

    p.drawText(text_object)
    p.showPage()
    p.save()
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f"Voiced_Report_{bill.id}.pdf")


@login_required
def generate_write_up(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id)

    # Restriction: No AI write-ups for closed bills
    if bill.current_status == Bill.Status.CLOSED:
        return JsonResponse({'error': 'Personal write-ups are disabled for closed bills.'}, status=403)

    user_vote = BillVote.objects.filter(bill=bill, user=request.user).first()
    if not user_vote or not user_vote.reason:
        return JsonResponse({'error': 'You must cast a vote with a reason to generate a submission.'}, status=400)

    try:
        client = get_gemini_client()
        config = types.GenerateContentConfig(
            system_instruction="Draft a formal petition letter to the Clerk of the National Assembly of Kenya. Return JSON with key: 'draft'.",
            response_mime_type="application/json",
            temperature=0.5
        )

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"Bill: {bill.title}\nUser Opinion: {user_vote.reason}",
            config=config
        )
        draft_data = json.loads(response.text)
        return JsonResponse({'draft': draft_data['draft']})
    except Exception:
        return JsonResponse({'error': 'Draft generation failed.'}, status=503)