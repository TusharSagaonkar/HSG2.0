from io import BytesIO

import qrcode
from django.core.files.base import ContentFile


def generate_vehicle_verification_qr(vehicle):
    qr_image = qrcode.make(vehicle.get_verification_url())
    image_buffer = BytesIO()
    qr_image.save(image_buffer, format="PNG")
    return ContentFile(
        image_buffer.getvalue(),
        name=f"vehicle-verification-{vehicle.verification_token}.png",
    )


def generate_permit_verification_qr(permit):
    qr_image = qrcode.make(permit.get_verification_url())
    image_buffer = BytesIO()
    qr_image.save(image_buffer, format="PNG")
    return ContentFile(
        image_buffer.getvalue(),
        name=f"parking-permit-verification-{permit.qr_token}.png",
    )
