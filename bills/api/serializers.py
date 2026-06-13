from rest_framework import serializers
from ..models import Bill

class BillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bill
        fields = [
            'id', 'title', 'source_url', 'government_level', 'county',
            'status',
            'ai_analysis', 'support_count', 'oppose_count', 'created_at'
        ]