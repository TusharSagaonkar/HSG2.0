Email Delivery
======================================================================

Overview
----------------------------------------------------------------------

The application now treats email delivery as a platform service instead of
scattered ``send_mail`` calls.

The delivery flow is:

::

   Global email settings
       -> optional society override
       -> email configuration resolver
       -> direct send or queued send
       -> SMTP backend

There are two configuration layers:

1. Global email settings
2. Society email settings

Resolution rules:

1. Authentication email always uses global settings
2. Non-authentication email uses society settings when active
3. Otherwise the system falls back to global settings


Data Model
----------------------------------------------------------------------

The email subsystem uses these models:

``GlobalEmailSettings``
   The default SMTP configuration for the whole platform. Only one active
   record should exist.

``SocietyEmailSettings``
   An optional society-level override. When ``is_active`` is false, the society
   falls back to global email settings.

``EmailTemplate``
   Stores reusable subject/body templates and variable metadata.

``EmailQueue``
   Stores queued outbound email records for non-auth or application-triggered
   email workflows.

``EmailLog``
   Stores delivery attempts for queued email.


Password Storage
----------------------------------------------------------------------

SMTP passwords are not stored in plain text when saved through the application.
They are encrypted before being written to the database.

Settings involved:

``DJANGO_EMAIL_SETTINGS_ENCRYPTION_KEY``
   Optional explicit encryption key for email secrets.

If that setting is not provided, the application derives a key from
``SECRET_KEY``.

Legacy behavior:

* Plain text passwords already stored in the database are still accepted
  so existing setups do not hard-fail.
* If a stored encrypted password cannot be decrypted with the current key,
  the app raises a configuration error. Re-save the password from the email
  settings form to fix it.


Global Configuration
----------------------------------------------------------------------

Global email configuration is used for:

* signup verification
* account verification
* OTP and authentication flows
* any non-society mail when no society override exists

To configure the global default:

1. Open Django admin
2. Go to ``Global email settings``
3. Create or update the active record
4. Enter SMTP host, port, username, password, and sender details

Important:

* Only one active global email settings record should exist
* Authentication mail depends on this record


Society Configuration
----------------------------------------------------------------------

Each society can manage its own SMTP override from the frontend.

Frontend entry points:

* Society detail page
* Society list page

Route:

::

   /housing/societies/<society_id>/email-settings/

Behavior:

* If override is enabled, society billing/receipt/notice email uses that SMTP
  configuration
* If override is disabled, society email falls back to the global configuration
* Authentication email never uses the society override


Using Email in Application Code
----------------------------------------------------------------------

Do not call Django's email backend directly from business logic unless you are
intentionally bypassing the platform delivery layer.

Use one of the service entry points in ``notifications.services``.

Direct authentication/global mail
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use this for authentication-style mail or for immediate global send behavior.

::

   from django.core.mail import EmailMultiAlternatives

   from notifications.models import EmailQueue
   from notifications.services import send_direct_email_message

   message = EmailMultiAlternatives(
       subject="Verify your account",
       body="Welcome to GrihaLekha.",
       to=["member@example.com"],
   )
   send_direct_email_message(
       message,
       email_type=EmailQueue.EmailType.AUTHENTICATION,
   )

Queued application mail
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use this for billing, receipts, notices, and any normal application email.

::

   from notifications.models import EmailQueue
   from notifications.services import queue_email

   queue_email(
       society_id=society.id,
       recipient_email="member@example.com",
       subject="Receipt available",
       body="Your payment receipt is ready.",
       email_type=EmailQueue.EmailType.RECEIPT,
   )

Template-based queueing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

   queue_email(
       society_id=society.id,
       recipient_email="member@example.com",
       template_id=template.id,
       context={
           "member_name": member.full_name,
           "invoice_number": bill.bill_number,
       },
       email_type=EmailQueue.EmailType.BILLING,
   )


Processing Queued Email
----------------------------------------------------------------------

The current repository does not yet have Celery wired in for email processing.

Right now, queued email is processed through the service layer or the
management command:

::

   uv run python manage.py process_email_queue

Optional limit:

::

   uv run python manage.py process_email_queue --limit 50

Internally this uses ``process_email_queue()`` from
``notifications.services``.


Authentication Email Path
----------------------------------------------------------------------

Allauth account email has been integrated with the global email service.

The custom account adapter:

* routes auth mail through the global email resolver
* falls back to Django's configured email backend if the database-backed
  configuration is unreadable

This avoids signup hard-failures caused by unreadable stored SMTP secrets.


Operational Notes
----------------------------------------------------------------------

Queued email:

* creates delivery logs in ``EmailLog``
* records which SMTP config was used in ``EmailQueue.smtp_used``
* respects retry status and retry count

Authentication email:

* is sent directly
* does not currently create queue or delivery log rows

If you need a full audit trail for authentication email as well, extend the
adapter path to write explicit audit entries.


Recommended Usage Rules
----------------------------------------------------------------------

Use these rules consistently:

* Use ``send_direct_email_message`` only for authentication/global email
* Use ``queue_email`` for billing, receipts, reminders, and notices
* Do not duplicate SMTP selection logic in app code
* Let the resolver decide whether global or society config should be used
* Re-save stored SMTP passwords after changing the encryption key or
  ``SECRET_KEY``
