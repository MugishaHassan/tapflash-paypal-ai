import json

from django.contrib import admin
from django.contrib.admin.utils import model_ngettext
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import Truncator

from tapflash.admin_mixins import ReadOnlyAdminMixin

from .models import Conversation, IncomingMessage, OutgoingReply, ProblemReport

REPORT_FIELDS = ('created_at', 'facility_name', 'restroom', 'facility_code', 'step', 'message', 'phone', 'user_id')


def pretty_json(value):
    text = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)
    return format_html('<pre style="margin: 0; white-space: pre-wrap;">{}</pre>', text)


@admin.register(ProblemReport)
class ProblemReportAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'facility_name', 'summary', 'status', 'phone', 'resolved_at')
    list_filter = ('status', 'created_at')
    search_fields = ('message', 'facility_name', 'phone', 'user_id', 'staff_notes')
    ordering = ('-created_at',)
    fieldsets = (
        ('Report', {'fields': REPORT_FIELDS}),
        ('Follow-up', {'fields': ('status', 'staff_notes', 'resolved_at')}),
    )
    # Staff only follow a report up: what the user sent stays as it was.
    readonly_fields = (*REPORT_FIELDS, 'resolved_at')
    actions = ('mark_looking', 'mark_resolved')

    def has_add_permission(self, request):
        return False

    @admin.display(description='problem')
    def summary(self, report):
        return Truncator(report.message).chars(70)

    @admin.action(description='Mark selected reports as being looked into')
    def mark_looking(self, request, queryset):
        count = queryset.update(status=ProblemReport.Status.LOOKING, resolved_at=None)
        self.message_user(request, f'{count} {model_ngettext(self.opts, count)} marked as being looked into.')

    @admin.action(description='Mark selected reports as resolved')
    def mark_resolved(self, request, queryset):
        count = queryset.exclude(status=ProblemReport.Status.RESOLVED).update(
            status=ProblemReport.Status.RESOLVED, resolved_at=timezone.now()
        )
        self.message_user(request, f'{count} {model_ngettext(self.opts, count)} marked as resolved.')


@admin.register(Conversation)
class ConversationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('user_id', 'status', 'step', 'last_seen', 'keep_until')
    list_filter = ('status', 'step')
    search_fields = ('user_id',)
    ordering = ('-last_seen',)
    fields = ('user_id', 'status', 'step', 'last_seen', 'keep_until', 'last_answered_at', 'last_key', 'chat_state')
    readonly_fields = ('chat_state',)

    @admin.display(description='state')
    def chat_state(self, conversation):
        return pretty_json(conversation.state)


@admin.register(IncomingMessage)
class IncomingMessageAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('received_at', 'user_id', 'status', 'handled_at', 'wa_message_id')
    list_filter = ('status', 'received_at')
    search_fields = ('wa_message_id', 'user_id')
    ordering = ('-id',)
    fields = ('wa_message_id', 'user_id', 'received_at', 'status', 'handled_at', 'error', 'message')
    readonly_fields = ('message',)

    @admin.display(description='payload')
    def message(self, incoming):
        return pretty_json(incoming.payload)


@admin.register(OutgoingReply)
class OutgoingReplyAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('created_at', 'user_id', 'channel', 'sent_at')
    list_filter = ('channel', 'created_at')
    search_fields = ('user_id',)
    ordering = ('-created_at',)
