class ReadOnlyAdminMixin:
    """For records the bot writes: staff can look at them but not add or change them."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
