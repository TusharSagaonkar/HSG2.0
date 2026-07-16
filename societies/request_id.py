"""Request ID middleware for audit correlation.

Generates a unique request ID for each request and makes it available
on the request object and in the response headers. This ID is used by
AuditLog entries to correlate all actions within a single request.
"""

import uuid


class RequestIDMiddleware:
    """Generates and propagates a unique request ID for audit correlation.

    - Reads X-Request-ID header if present (from upstream proxy/load balancer).
    - Otherwise generates a UUID4.
    - Sets request.request_id for use by views and audit logging.
    - Adds X-Request-ID to the response headers.
    """

    HEADER = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get(self.HEADER) or str(uuid.uuid4())
        request.request_id = request_id

        response = self.get_response(request)

        response[self.HEADER] = request_id
        return response
