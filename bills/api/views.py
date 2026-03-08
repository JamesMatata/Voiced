from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from ..models import Bill
from .serializers import BillSerializer


class BillViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Provides standard REST actions (list, retrieve) for Next.js.
    Only exposes bills that have been processed by the AI.
    """
    queryset = Bill.objects.filter(status=Bill.Status.ACTIVE).order_by('-created_at')
    serializer_class = BillSerializer

    @action(detail=True, methods=['post'])
    def vote(self, request, pk=None):
        """Custom endpoint to handle Up/Down votes."""
        bill = self.get_object()
        vote_type = request.data.get('vote')

        if vote_type == 'support':
            bill.support_count += 1
        elif vote_type == 'oppose':
            bill.oppose_count += 1
        else:
            return Response({'error': 'Invalid vote type'}, status=400)

        bill.save()
        return Response({'status': 'vote recorded', 'support': bill.support_count, 'oppose': bill.oppose_count})