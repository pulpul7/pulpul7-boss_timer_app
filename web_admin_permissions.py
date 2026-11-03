"""Web-only grants bound to a signed member browser, never a typed nickname."""


def registered_web_admin(access, context):
    if (not context or not context.get('authenticated') or context.get('blocked')
            or context.get('role') != 'member' or not context.get('visitor')):
        return False
    with access.lock:
        return context['visitor'] in getattr(access, 'settings', {}).get('web_admins', {})
