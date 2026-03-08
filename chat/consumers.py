import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import ChatMessage, MessageReaction
from bills.models import Bill
from django.contrib.auth.models import User


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if not self.scope['user'].is_authenticated:
            await self.close()
            return

        self.bill_id = self.scope['url_route']['kwargs']['bill_id']
        self.room_group_name = f'chat_{self.bill_id}'

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.scope['user'].is_authenticated:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        action_type = data.get('type')
        user = self.scope['user']

        if action_type == 'new_message':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'message_id': data.get('message_id'),
                    'content': data.get('content'),
                    'sender_alias': data.get('sender_alias'),
                    'user_id': user.id,
                    'parent_id': data.get('parent_id')
                }
            )

        if action_type == 'reaction':
            msg_id = data.get('message_id')
            r_type = data.get('reaction_type')

            up, down, final = await self.handle_reaction(msg_id, r_type, user)

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'message_reaction',
                    'message_id': msg_id,
                    'upvotes': up,
                    'downvotes': down,
                    'reaction_type': final,
                    'user_id': user.id
                }
            )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def message_reaction(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def handle_reaction(self, message_id, reaction_type, user):
        from chat.models import ChatMessage, MessageReaction
        from django.db import transaction

        with transaction.atomic():
            message = ChatMessage.objects.select_for_update().get(id=message_id)

            if message.user == user:
                return message.upvotes, message.downvotes, 'self_vote_error'

            existing = MessageReaction.objects.filter(message=message, user=user).first()
            final_type = reaction_type

            if existing:
                if existing.reaction_type == reaction_type:
                    existing.delete()
                    final_type = 'none'
                else:
                    existing.reaction_type = reaction_type
                    existing.save()
            else:
                MessageReaction.objects.create(message=message, user=user, reaction_type=reaction_type)

            upvotes = MessageReaction.objects.filter(message=message, reaction_type='up').count()
            downvotes = MessageReaction.objects.filter(message=message, reaction_type='down').count()

            message.upvotes = upvotes
            message.downvotes = downvotes
            message.save()

        return upvotes, downvotes, final_type