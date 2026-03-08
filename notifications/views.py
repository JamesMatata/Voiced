import json
from django.http import JsonResponse
from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import Notification


class NotificationListView(LoginRequiredMixin, ListView):
    model = Notification
    template_name = 'notifications/notification_list.html'
    context_object_name = 'notifications'
    paginate_by = 15

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    def post(self, request, *args, **kwargs):
        data = json.loads(request.body)
        action = data.get('action')
        notification_id = data.get('id')

        if action == 'mark_all_read':
            Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
            return JsonResponse({'status': 'success'})

        elif action == 'mark_read':
            Notification.objects.filter(user=request.user, id=notification_id).update(is_read=True)
            return JsonResponse({'status': 'success'})

        elif action == 'delete':
            Notification.objects.filter(user=request.user, id=notification_id).delete()
            return JsonResponse({'status': 'success'})

        return JsonResponse({'status': 'error'}, status=400)