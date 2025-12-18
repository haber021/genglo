from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages
from .models import SentDailyReport


class SecureAdminSite(admin.AdminSite):
    """Custom admin site that enforces authentication and admin user verification"""
    
    def has_permission(self, request):
        """
        Check if the user has permission to access the admin site.
        Only superusers and members with admin role can access Django admin.
        Staff users (is_staff but not superuser) and Member role 'staff' are NOT allowed.
        """
        if not request.user.is_authenticated:
            return False
        
        # Import here to avoid circular imports
        from admin_panel.views import can_access_django_admin
        return can_access_django_admin(request.user)
    
    def admin_view(self, view, cacheable=False):
        """
        Override admin_view to add authentication check and redirect to login if needed.
        """
        def inner(request, *args, **kwargs):
            # Check if user is authenticated
            if not request.user.is_authenticated:
                messages.warning(request, 'Please log in to access the admin panel.')
                login_url = reverse('root_login')
                next_url = request.get_full_path()
                return redirect(f'{login_url}?next={next_url}')
            
            # Check if user has admin permissions
            if not self.has_permission(request):
                messages.error(request, 'You do not have permission to access the admin panel.')
                return redirect('root_login')
            
            # Call the original view
            return view(request, *args, **kwargs)
        
        return inner


# Override the default admin site to add authentication check
class SecureDefaultAdminSite(admin.AdminSite):
    """Wrapper around default admin site that adds authentication verification"""
    
    def has_permission(self, request):
        """Check if user is authenticated and can access Django admin"""
        if not request.user.is_authenticated:
            return False
        
        # Import here to avoid circular imports
        from admin_panel.views import can_access_django_admin
        return can_access_django_admin(request.user)
    
    def admin_view(self, view, cacheable=False):
        """Override admin_view to add authentication check"""
        def inner(request, *args, **kwargs):
            # Check if user is authenticated
            if not request.user.is_authenticated:
                messages.warning(request, 'Please log in to access the admin panel.')
                login_url = reverse('root_login')
                next_url = request.get_full_path()
                return redirect(f'{login_url}?next={next_url}')
            
            # Check if user has admin permissions
            if not self.has_permission(request):
                messages.error(request, 'You do not have permission to access the admin panel.')
                return redirect('root_login')
            
            # Call the original view from default admin site
            return view(request, *args, **kwargs)
        
        return inner


# Create secure admin site instance
secure_admin_site = SecureAdminSite(name='secure_admin')

# Register models with the secure admin site
@admin.register(SentDailyReport, site=secure_admin_site)
class SentDailyReportAdmin(admin.ModelAdmin):
    list_display = ('report_date', 'recipient_email', 'sent_at')
    list_filter = ('report_date', 'recipient_email', 'sent_at')
    search_fields = ('recipient_email',)
    readonly_fields = ('sent_at',)
    date_hierarchy = 'report_date'
    
    def has_add_permission(self, request):
        # Prevent manual creation - reports should only be created when sent
        return False
    
    def has_change_permission(self, request, obj=None):
        # Prevent editing - sent reports should be immutable
        return False
