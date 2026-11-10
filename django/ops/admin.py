from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import Attendant, Cleaner, CleaningCheck


@admin.register(Cleaner)
class CleanerAdmin(admin.ModelAdmin):
    list_display = ('name', 'restroom', 'phone', 'payout_email', 'bonus', 'is_active')
    list_filter = ('is_active', 'restroom')
    search_fields = ('name', 'phone', 'payout_email')
    autocomplete_fields = ('restroom',)


@admin.register(CleaningCheck)
class CleaningCheckAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'cleaner', 'restroom', 'status', 'score', 'decided_by', 'payout')
    list_filter = ('status', 'restroom')
    search_fields = ('cleaner__name', 'ai_note')
    ordering = ('-created_at',)
    fields = ('picture', 'cleaner', 'restroom', 'status', 'score', 'ai_note', 'decided_by', 'payout', 'created_at')
    readonly_fields = fields

    @admin.display(description='photo')
    def picture(self, check):
        return format_html('<img src="{}" alt="Cleaning photo" style="max-width:360px;border-radius:8px">',
                           reverse('ops-photo', args=[check.pk]))

    def has_add_permission(self, request):
        return False


@admin.register(Attendant)
class AttendantAdmin(admin.ModelAdmin):
    list_display = ('user', 'restroom')
    autocomplete_fields = ('restroom',)
    search_fields = ('user__username', 'restroom__name')


from .models import EmptyingOrder, OperatorChat, SensorReading  # noqa: E402

admin.site.register(OperatorChat, list_display=('restroom', 'user_id', 'linked_at'))
admin.site.register(SensorReading, list_display=('at', 'restroom', 'kind', 'value', 'sandbox'), list_filter=('kind', 'sandbox'))
admin.site.register(EmptyingOrder, list_display=('created_at', 'restroom', 'level', 'status', 'price', 'capture_id'), list_filter=('status',))
