from django.contrib import admin
from django.urls import path, include

admin.site.site_header = "VOICED - The Nation Is Talking"
admin.site.site_title = "VOICED Admin Portal"
admin.site.index_title = "Welcome to the National Debate Dashboard"

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('bills.urls')),
    path('', include('core.urls')),
    path('auth/', include('accounts.urls')),
    path('', include('chat.urls')),
    path('notifications/', include('notifications.urls')),
    path('engagement/', include('engagement.urls')),
]