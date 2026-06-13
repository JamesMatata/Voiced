from django.contrib import admin
from django.urls import path, include

from accounts.views import wallet_view
from core.views import set_language_preference

admin.site.site_header = "VOICED - The Nation Is Talking"
admin.site.site_title = "VOICED Admin Portal"
admin.site.index_title = "Welcome to the National Debate Dashboard"

urlpatterns = [
    path('admin/', admin.site.urls),
    path('i18n/setlang/', set_language_preference, name='set_language'),
    path('api/', include('bills.urls')),
    path('payments/', include('payments.urls')),
    path('auth/social/', include('allauth.urls')),
    path('wallet/', wallet_view, name='wallet'),
    path('', include('core.urls')),
    path('auth/', include('accounts.urls')),
    path('', include('chat.urls')),
    path('notifications/', include('notifications.urls')),
    path('engagement/', include('engagement.urls')),
]