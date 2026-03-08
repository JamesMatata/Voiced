from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('bills.urls')),
    path('', include('core.urls')),
    path('auth/', include('accounts.urls')),
    path('', include('chat.urls')),
    path('notifications/', include('notifications.urls')),
]