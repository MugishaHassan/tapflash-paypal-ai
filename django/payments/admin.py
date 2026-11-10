from django.contrib import admin
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from bot.formatting import format_money
from tapflash.admin_mixins import ReadOnlyAdminMixin

from .models import AccessPass, Payment, PayPalOrder, Payout, Refund, VisitPass


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('created_at', 'reference', 'status', 'method', 'phone', 'facility_name', 'amount')
    # The date filter (today, past 7 days, ...) stands in for date_hierarchy,
    # which on MySQL needs time zone tables that shared hosting often lacks.
    list_filter = ('status', 'method', 'created_at')
    search_fields = ('reference', 'phone', 'user_id', 'facility_name')
    ordering = ('-created_at',)
    fields = (
        'created_at', 'reference', 'status', 'failure_reason', 'user_id', 'method', 'phone', 'provider',
        'restroom', 'facility_code', 'facility_name', 'price', 'fee', 'total', 'currency', 'access_pass_link',
    )
    readonly_fields = ('access_pass_link',)

    # Money records are kept.
    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='total', ordering='total')
    def amount(self, payment):
        return format_money(payment.total, payment.currency)

    @admin.display(description='access pass')
    def access_pass_link(self, payment):
        try:
            access_pass = payment.access_pass
        except AccessPass.DoesNotExist:
            return None
        url = reverse('admin:payments_accesspass_change', args=[access_pass.pk])
        return format_html('<a href="{}">Access code {}</a>', url, access_pass.otp)


class ActiveNowFilter(admin.SimpleListFilter):
    title = 'active now'
    parameter_name = 'active'

    def lookups(self, request, model_admin):
        return [('yes', 'Yes'), ('no', 'No')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.active()
        if self.value() == 'no':
            return queryset.filter(expires_at__lte=timezone.now())
        return queryset


@admin.register(AccessPass)
class AccessPassAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('issued_at', 'reference', 'restroom_name', 'otp', 'expires_at', 'active_now')
    list_filter = (ActiveNowFilter, 'issued_at')
    list_select_related = ('payment',)
    search_fields = ('payment__reference', 'otp', 'user_id')
    ordering = ('-issued_at',)
    fields = ('payment', 'user_id', 'otp', 'issued_at', 'expires_at', 'active_now')
    readonly_fields = ('active_now',)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='reference', ordering='payment__reference')
    def reference(self, access_pass):
        return access_pass.payment.reference

    @admin.display(description='restroom', ordering='payment__facility_name')
    def restroom_name(self, access_pass):
        return access_pass.payment.facility_name

    @admin.display(boolean=True, description='active now')
    def active_now(self, access_pass):
        return access_pass.is_active()


@admin.register(PayPalOrder)
class PayPalOrderAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('created_at', 'order_id', 'status', 'facility_name', 'paypal_amount', 'payer_email', 'channel')
    list_filter = ('status', 'channel', 'created_at')
    search_fields = ('order_id', 'capture_id', 'payer_email', 'user_id', 'facility_name')
    ordering = ('-created_at',)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='amount', ordering='amount')
    def paypal_amount(self, order):
        return format_money(order.amount, order.currency)


@admin.register(Refund)
class RefundAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('created_at', 'payment', 'refund_amount', 'status', 'decided_by', 'category', 'reason')
    list_filter = ('status', 'decided_by', 'category', 'created_at')
    search_fields = ('payment__reference', 'provider_refund_id', 'reason')
    ordering = ('-created_at',)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='amount', ordering='amount')
    def refund_amount(self, refund):
        return format_money(refund.amount, refund.currency)


@admin.register(Payout)
class PayoutAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('created_at', 'kind', 'restroom', 'receiver', 'payout_amount', 'visits', 'status')
    list_filter = ('kind', 'status', 'created_at')
    search_fields = ('sender_batch_id', 'batch_id', 'receiver')
    ordering = ('-created_at',)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='amount', ordering='amount')
    def payout_amount(self, payout):
        return format_money(payout.amount, payout.currency)


@admin.register(VisitPass)
class VisitPassAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user_id', 'status', 'visits_total', 'visits_left', 'price', 'currency', 'paypal_fee', 'capture_id')
    list_filter = ('status',)
    search_fields = ('user_id', 'capture_id', 'paypal_order_id')
    readonly_fields = ('token', 'paypal_order_id', 'capture_id', 'paypal_fee', 'paid_at')
