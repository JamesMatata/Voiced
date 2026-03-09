import json
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from bills.models import Bill
from accounts.models import UserProfile
from chat.models import ChatMessage, MessageReaction

from .moderation import check_message_toxicity


class BillChatView(LoginRequiredMixin, DetailView):
    model = Bill
    template_name = 'chat/chat_room.html'
    context_object_name = 'bill'
    login_url = '/auth/login/'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        if not hasattr(user, 'profile'):
            UserProfile.objects.create(user=user)
            user.refresh_from_db()

        context['user_alias'] = user.profile.chat_alias
        context['user_id'] = user.id
        context['is_closed'] = self.object.current_status == Bill.Status.CLOSED

        if self.object.closing_date:
            context['closing_date_iso'] = self.object.closing_date.isoformat()

        messages = self.object.messages.select_related(
            'user__profile', 'parent_message'
        ).order_by('created_at')[:100]

        user_reactions = MessageReaction.objects.filter(
            user=user, message__in=messages
        ).values_list('message_id', 'reaction_type')

        context['upvoted_ids'] = [m_id for m_id, r_type in user_reactions if r_type == 'up']
        context['downvoted_ids'] = [m_id for m_id, r_type in user_reactions if r_type == 'down']
        context['messages'] = messages
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.current_status == Bill.Status.CLOSED:
            return JsonResponse({'status': 'rejected', 'message': 'Chat is read-only.'}, status=403)

        try:
            data = json.loads(request.body)
            content = data.get('message', '').strip()
            parent_id = data.get('parent_id')
        except:
            content = request.POST.get('message', '').strip()
            parent_id = request.POST.get('parent_id')

        if not content:
            return JsonResponse({'status': 'error', 'message': 'Empty message.'}, status=400)

        is_toxic, reason = check_message_toxicity(content)
        if is_toxic:
            return JsonResponse({'status': 'rejected', 'message': reason}, status=403)

        msg = self.object.messages.create(
            user=request.user,
            content=content,
            parent_message_id=parent_id
        )

        parent_content = ""
        if msg.parent_message:
            parent_content = msg.parent_message.content

        return JsonResponse({
            'status': 'success',
            'msg_id': msg.id,
            'content': msg.content,
            'user_alias': request.user.profile.chat_alias,
            'parent_content': parent_content
        })