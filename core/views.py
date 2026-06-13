import csv
import logging
from django.db.models import Count, F, Max, Q
from django.views.generic import ListView, DetailView, TemplateView
from django.utils import timezone
from django.http import JsonResponse, Http404, HttpResponse
from django.db import transaction
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils.translation import get_language
from datetime import timedelta
from django.core.paginator import Paginator
from django.conf import settings
from django.utils import translation
from django.views.decorators.http import require_POST
from django.utils.http import url_has_allowed_host_and_scheme

from bills.models import Bill, BillVote
from bills.services.scraper import KENYA_COUNTIES
from bills.services.localization import resolve_bill_language_payload
from bills.tasks import send_vote_feedback_sms_task
from bills.utils import generate_bill_baraza_poster_pdf, generate_bill_baraza_poster_png
from chat.models import ChatMessage

logger = logging.getLogger(__name__)

_COUNTY_OPTIONS_SORTED = tuple(sorted(KENYA_COUNTIES, key=lambda c: c.lower()))


def _normalize_county_param(raw, county_choices=_COUNTY_OPTIONS_SORTED):
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw in county_choices:
        return raw
    lower = raw.lower()
    for c in county_choices:
        if c.lower() == lower:
            return c
    return ""


@require_POST
def set_language_preference(request):
    lang = (request.POST.get("language") or "").split("-")[0].strip().lower()
    supported = {code.split("-")[0] for code, _ in settings.LANGUAGES}
    if lang not in supported:
        lang = (settings.LANGUAGE_CODE or "en").split("-")[0]

    request.session[settings.LANGUAGE_COOKIE_NAME] = lang
    if request.user.is_authenticated and hasattr(request.user, "profile"):
        request.user.profile.language = lang
        request.user.profile.save(update_fields=["language"])

    translation.activate(lang)
    request.LANGUAGE_CODE = lang

    next_url = (request.POST.get("next") or request.META.get("HTTP_REFERER") or "/").strip()
    if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
        next_url = "/"
    return redirect(next_url)


class HomeView(TemplateView):
    template_name = 'core/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Only show published bills on Home
        context['latest_bills'] = Bill.objects.active_bills().order_by('-created_at')[:3]

        context['trending_discussions'] = Bill.objects.active_bills().filter(messages__isnull=False).annotate(
            last_message=Max('messages__created_at')
        ).order_by('-last_message')[:3]

        return context


class BillListView(ListView):
    model = Bill
    template_name = 'core/bill_list.html'
    context_object_name = 'ongoing_bills'
    paginate_by = 20

    def _parse_list_filters(self):
        filter_type = self.request.GET.get('status') or self.request.GET.get('filter', 'ongoing')
        if filter_type not in ('ongoing', 'closed', 'all'):
            filter_type = 'ongoing'
        level_filter = (self.request.GET.get('level') or 'all').lower()
        if level_filter not in ('all', 'national', 'county'):
            level_filter = 'all'
        search_query = (self.request.GET.get('q') or '').strip()
        raw_county = (self.request.GET.get('county') or '').strip()
        county_key = ''
        if level_filter == 'county':
            county_key = _normalize_county_param(raw_county)
        return filter_type, level_filter, search_query, county_key

    def _apply_bill_list_filters(self, qs, filter_type, level_filter, search_query, county_key):
        today = timezone.now().date()
        threshold = today - timedelta(days=30)

        if level_filter == 'national':
            qs = qs.filter(government_level=Bill.GovernmentLevel.NATIONAL)
        elif level_filter == 'county':
            qs = qs.filter(government_level=Bill.GovernmentLevel.COUNTY)
            if county_key:
                qs = qs.filter(county__iexact=county_key)

        if search_query:
            qs = qs.filter(
                Q(title__icontains=search_query)
                | Q(ai_analysis__english__short_summary__icontains=search_query)
                | Q(ai_analysis_summary_en__icontains=search_query)
                | Q(summary_en__icontains=search_query)
            )

        if filter_type == 'ongoing':
            qs = qs.filter(Q(closing_date__isnull=True) | Q(closing_date__gte=today))
        elif filter_type == 'closed':
            qs = qs.filter(closing_date__lt=today, closing_date__gte=threshold)
        elif filter_type == 'all':
            qs = qs.filter(
                Q(closing_date__isnull=True) |
                Q(closing_date__gte=today) |
                Q(closing_date__gte=threshold, closing_date__lt=today)
            )

        return qs

    def get_queryset(self):
        ft, lv, sq, ck = self._parse_list_filters()
        # Public list: published bills only (do not require AI completion—many stay hidden otherwise).
        qs = Bill.objects.filter(status=Bill.Status.PUBLISHED)
        qs = self._apply_bill_list_filters(qs, ft, lv, sq, ck)
        return qs.order_by('-created_at')

    def get(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            # We bypass pagination for search/filter results to ensure everything shows
            queryset = self.get_queryset()
            active_status = request.GET.get('status') or request.GET.get('filter', 'ongoing')
            data = []
            for b in queryset:
                data.append({
                    'id': str(b.id),
                    'title': b.title,
                    'created_at': b.created_at.strftime("%b %d, %Y"),
                    'status': b.current_status,
                    'summary': b.list_card_summary,
                    'support_count': b.support_count,
                    'oppose_count': b.oppose_count,
                    'view_count': b.view_count,
                    'detail_url': f"/bills/{b.id}/",
                    'government_level': b.government_level,
                    'county': b.county or '',
                })
            return JsonResponse({'bills': data, 'active_status': active_status})
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ft, lv, sq, ck = self._parse_list_filters()
        context['current_status_filter'] = ft
        context['current_level_filter'] = lv
        context['current_search_query'] = sq
        context['county_options'] = list(_COUNTY_OPTIONS_SORTED)
        context['current_county_filter'] = ck if lv == 'county' else ''

        today = timezone.now().date()
        hot_qs = Bill.objects.filter(
            status=Bill.Status.PUBLISHED,
            closing_date__gte=today,
            view_count__gte=100,
        )
        hot_qs = self._apply_bill_list_filters(hot_qs, ft, lv, sq, ck)
        context['hot_bills'] = hot_qs.order_by('-view_count')[:3]
        return context


class BillDetailView(DetailView):
    model = Bill
    template_name = 'core/bill_detail.html'
    context_object_name = 'bill'

    def get(self, request, *args, **kwargs):
        bill = get_object_or_404(Bill, pk=kwargs.get('pk'), status=Bill.Status.PUBLISHED)
        if bill.is_archived:
            return redirect(f"{reverse('bill_list')}?archived=1")
        return super().get(request, *args, **kwargs)

    def get_object(self):
        obj = super().get_object()
        if obj.status != Bill.Status.PUBLISHED:
            raise Http404("This bill is not publicly available.")

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
        localized = resolve_bill_language_payload(bill, get_language())
        outcome = getattr(bill, "outcome", None)
        context['is_closed'] = bill.is_closed
        context['bill_outcome'] = outcome
        context["localized_bill"] = localized
        context["translation_fallback_notice"] = localized.get("fallback_message") or ""
        context['community_votes'] = bill.support_count + bill.oppose_count
        context['official_verified_votes'] = bill.verified_support_count + bill.verified_oppose_count
        vote_user_ids = set(BillVote.objects.filter(bill=bill).values_list("user_id", flat=True))
        chat_user_ids = set(ChatMessage.objects.filter(bill=bill).values_list("user_id", flat=True))
        total_participants = len(vote_user_ids | chat_user_ids)
        context['total_participants'] = total_participants

        if bill.closing_date:
            context['closing_date_iso'] = bill.closing_date.isoformat()

        user_vote = None
        is_kenyan_user = False
        if self.request.user.is_authenticated:
            user_vote = BillVote.objects.filter(bill=bill, user=self.request.user).first()
            is_kenyan_user = bool(getattr(self.request.user.profile, "is_kenyan", False))
            user_commented = ChatMessage.objects.filter(bill=bill, user=self.request.user).exists()
            if user_vote:
                context["user_participation_kind"] = "Official" if is_kenyan_user else "Pulse"
            elif user_commented:
                context["user_participation_kind"] = "Discussion"
            else:
                context["user_participation_kind"] = ""
            context["user_participated"] = bool(user_vote or user_commented)
        context['user_vote'] = user_vote
        context['is_kenyan_user'] = is_kenyan_user
        preferred_lang = self.request.user.profile.language if self.request.user.is_authenticated else "en"
        if preferred_lang == "sh":
            preferred_lang = "sr"
        if preferred_lang not in {"en", "sw", "sr"}:
            preferred_lang = "en"
        context['preferred_lang'] = preferred_lang

        if self.request.user.is_authenticated:
            from accounts.models import Wallet
            from payments.constants import LEGAL_DRAFT_PRICE, REPORT_PRICE, VERIFICATION_FEE
            from payments.models import Purchase, Transaction

            wallet, _ = Wallet.objects.get_or_create(user=self.request.user)
            context['wallet_balance'] = wallet.balance
            context['wallet_available'] = wallet.available_balance
            context['report_price'] = REPORT_PRICE
            context['legal_draft_price'] = LEGAL_DRAFT_PRICE
            context['verification_fee'] = VERIFICATION_FEE
            context['has_report_purchase'] = Purchase.objects.filter(
                user=self.request.user, bill=bill, service_type=Transaction.ServiceType.REPORT
            ).exists()
            context['has_draft_purchase'] = Purchase.objects.filter(
                user=self.request.user, bill=bill, service_type=Transaction.ServiceType.DRAFT
            ).exists()
        else:
            context['wallet_balance'] = None
            context['wallet_available'] = None
            context['report_price'] = None
            context['legal_draft_price'] = None
            context['verification_fee'] = None
            context['has_report_purchase'] = False
            context['has_draft_purchase'] = False

        return context

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'status': 'error', 'message': 'Not authenticated'}, status=403)

        bill = self.get_object()
        if hasattr(bill, "outcome"):
            return JsonResponse({'status': 'error', 'message': 'This bill now has a final outcome. Voting is closed.'}, status=403)
        if bill.is_closed:
            return JsonResponse({'status': 'error', 'message': 'This bill is closed. Voting is no longer active.'}, status=403)

        vote_type = request.POST.get('vote_type')
        reason = request.POST.get('reason', '')

        if vote_type in ['support', 'oppose']:
            bill = Bill.objects.select_for_update().get(pk=bill.pk)
            vote = BillVote.objects.filter(bill=bill, user=request.user).first()
            is_kenyan = bool(
                getattr(getattr(request.user, "profile", None), "is_kenyan", False)
            )

            created = False
            if not vote:
                vote = BillVote.objects.create(
                    bill=bill,
                    user=request.user,
                    vote_type=vote_type,
                    reason=reason,
                )
                created = True

                update_fields = {
                    "total_votes": F("total_votes") + 1,
                    "support_count": F("support_count") + 1 if vote_type == "support" else F("support_count"),
                    "oppose_count": F("oppose_count") + 1 if vote_type == "oppose" else F("oppose_count"),
                }
                if is_kenyan:
                    update_fields["verified_citizen_votes"] = F("verified_citizen_votes") + 1
                    if vote_type == "support":
                        update_fields["verified_support_count"] = F("verified_support_count") + 1
                    else:
                        update_fields["verified_oppose_count"] = F("verified_oppose_count") + 1
                Bill.objects.filter(pk=bill.pk).update(**update_fields)
            else:
                old_vote_type = vote.vote_type
                vote.vote_type = vote_type
                vote.reason = reason
                vote.save(update_fields=["vote_type", "reason"])

                if old_vote_type != vote_type:
                    update_fields = {
                        "support_count": F("support_count") + (1 if vote_type == "support" else -1),
                        "oppose_count": F("oppose_count") + (1 if vote_type == "oppose" else -1),
                    }
                    if is_kenyan:
                        update_fields["verified_support_count"] = F("verified_support_count") + (
                            1 if vote_type == "support" else -1
                        )
                        update_fields["verified_oppose_count"] = F("verified_oppose_count") + (
                            1 if vote_type == "oppose" else -1
                        )
                    Bill.objects.filter(pk=bill.pk).update(**update_fields)

            bill.refresh_from_db()
            verified_total = bill.verified_support_count + bill.verified_oppose_count
            if verified_total >= 50 and verified_total >= bill.last_report_vote_count + 50:
                Bill.objects.filter(pk=bill.pk).update(
                    last_report_vote_count=verified_total,
                    report_generation_in_progress=True,
                )
                from bills.tasks import generate_bill_report_pdf

                generate_bill_report_pdf.delay(str(bill.id))
                bill.refresh_from_db()

            msg = "Success! Your official vote is recorded and will be included in the National Pulse analysis for Parliament."
            if created and not is_kenyan:
                msg = "Pulse recorded! Your opinion is visible to the community, but it cannot be presented officially to Parliament until you verify your Kenyan identity."
            elif (not created) and not is_kenyan:
                msg = "Pulse recorded! Your opinion is visible to the community, but it cannot be presented officially to Parliament until you verify your Kenyan identity."

            try:
                send_vote_feedback_sms_task.delay(request.user.id, str(bill.id), vote.receipt_id)
            except Exception:
                logger.exception("Failed to queue vote feedback SMS for vote %s", vote.id)
            return JsonResponse(
                {
                    'status': 'success',
                    'vote_type': vote.vote_type,
                    'reason': vote.reason,
                    'receipt_id': vote.receipt_id,
                    'support_count': bill.support_count,
                    'oppose_count': bill.oppose_count,
                    'verified_citizen_votes': bill.verified_citizen_votes,
                    'message': msg,
                }
            )

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
        ).filter(msg_count__gt=0, status=Bill.Status.PUBLISHED)
        if filter_type == 'ongoing':
            queryset = base_qs.filter(Q(closing_date__isnull=True) | Q(closing_date__gte=today))
        elif filter_type == 'closed':
            queryset = base_qs.filter(closing_date__lt=today, closing_date__gte=threshold)
        else:
            queryset = base_qs.filter(
                Q(closing_date__isnull=True) |
                Q(closing_date__gte=today) |
                Q(closing_date__gte=threshold, closing_date__lt=today)
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


def bill_vote_counts(request, pk):
    bill = get_object_or_404(Bill, pk=pk)
    return render(request, 'core/partials/bill_vote_counts.html', {'bill': bill})


def national_pulse_status(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id, status=Bill.Status.PUBLISHED)
    total_votes = bill.support_count + bill.oppose_count
    verified_votes = bill.verified_support_count + bill.verified_oppose_count
    votes_needed = max(0, 50 - verified_votes)
    return JsonResponse({
        'total_votes': total_votes,
        'verified_votes': verified_votes,
        'votes_needed': votes_needed,
        'eligible': verified_votes >= 50,
        'is_generating': bill.report_generation_in_progress,
        'is_ready': bool(bill.pdf_report) and not bill.report_generation_in_progress
    })


def bill_ledger_view(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id, status=Bill.Status.PUBLISHED)
    q = (request.GET.get("q") or "").strip().lower()
    votes = (
        BillVote.objects.filter(bill=bill)
        .select_related("user__profile")
        .order_by("-created_at")
    )
    if q:
        votes = votes.filter(receipt_id__icontains=q)

    rows = [
        {
            "receipt_id": v.receipt_id,
            "vote_type": v.vote_type,
            "created_at": v.created_at,
            "verification_status": "Verified Citizen"
            if bool(getattr(getattr(v.user, "profile", None), "is_kenyan", False))
            else "Guest Pulse",
        }
        for v in votes
    ]
    paginator = Paginator(rows, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "core/bill_ledger.html",
        {
            "bill": bill,
            "page_obj": page_obj,
            "search_query": q,
            "total_rows": len(rows),
        },
    )


def bill_ledger_csv_export(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id, status=Bill.Status.PUBLISHED)
    votes = (
        BillVote.objects.filter(bill=bill)
        .select_related("user__profile")
        .order_by("created_at")
    )
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="ledger_{bill.short_id}.csv"'
    writer = csv.writer(response)
    writer.writerow(["receipt_id", "vote_direction", "timestamp", "verification_status"])
    for v in votes:
        verified = bool(getattr(getattr(v.user, "profile", None), "is_kenyan", False))
        writer.writerow(
            [
                v.receipt_id,
                v.get_vote_type_display(),
                v.created_at.isoformat(),
                "Verified Citizen" if verified else "Guest Pulse",
            ]
        )
    return response


def bill_ledger_verify_page(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id, status=Bill.Status.PUBLISHED)
    return render(
        request,
        "core/bill_ledger_verify.html",
        {
            "bill": bill,
            "prefill_receipt": (request.GET.get("q") or "").strip(),
        },
    )


def bill_ledger_lookup_json(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id, status=Bill.Status.PUBLISHED)
    q = (request.GET.get("q") or "").strip().lower()
    if not q:
        return JsonResponse({"ok": False, "error": "Receipt is required."}, status=400)

    vote = (
        BillVote.objects.filter(bill=bill, receipt_id__iexact=q)
        .select_related("user__profile")
        .first()
    )
    if not vote:
        return JsonResponse({"ok": True, "found": False})

    is_verified = bool(getattr(getattr(vote.user, "profile", None), "is_kenyan", False))
    return JsonResponse(
        {
            "ok": True,
            "found": True,
            "receipt_id": vote.receipt_id,
            "receipt_short": f"{vote.receipt_id[:4]}...{vote.receipt_id[-4:]}",
            "vote_direction": vote.get_vote_type_display(),
            "timestamp": vote.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "verification_status": "Verified Citizen" if is_verified else "Guest Pulse",
            "ledger_url": reverse("bill_ledger", kwargs={"bill_id": str(bill.id)}),
        }
    )


def bill_baraza_poster_pdf(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id, status=Bill.Status.PUBLISHED)
    data = generate_bill_baraza_poster_pdf(bill)
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="baraza_poster_{bill.short_id}.pdf"'
    return response


def bill_baraza_poster_png(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id, status=Bill.Status.PUBLISHED)
    data = generate_bill_baraza_poster_png(bill)
    response = HttpResponse(data, content_type="image/png")
    response["Content-Disposition"] = f'attachment; filename="baraza_poster_{bill.short_id}.png"'
    return response