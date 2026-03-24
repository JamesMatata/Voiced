import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist


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
            msg_id = data.get('message_id')
            if not msg_id:
                return

            message_payload = await self.get_message_payload(msg_id)
            if not message_payload:
                return

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'message_id': message_payload['message_id'],
                    'content': message_payload['content'],
                    'sender_alias': message_payload['sender_alias'],
                    'user_id': message_payload['user_id'],
                    'parent_id': message_payload['parent_id'],
                    'parent_content': message_payload['parent_content']
                }
            )

        if action_type == 'reaction':
            msg_id = data.get('message_id')
            r_type = data.get('reaction_type')

            if not msg_id or msg_id == "null":
                return

            up, down, final = await self.handle_reaction(msg_id, r_type, user)

            if up is not None:
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
    def get_message_payload(self, message_id):
        from chat.models import ChatMessage

        try:
            message = ChatMessage.objects.select_related('parent_message').get(
                id=message_id,
                bill_id=self.bill_id
            )
            return {
                'message_id': message.id,
                'content': message.content,
                'sender_alias': message.get_display_alias(),
                'user_id': message.user_id,
                'parent_id': message.parent_message_id,
                'parent_content': message.parent_message.content if message.parent_message else ""
            }
        except ChatMessage.DoesNotExist:
            return None

    @database_sync_to_async
    def handle_reaction(self, message_id, reaction_type, user):
        from chat.models import ChatMessage, MessageReaction

        try:
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
        except ObjectDoesNotExist:
            return None, None, 'error'
        except Exception:
            return None, None, 'error'