from io import BytesIO

import qrcode
from django.core.files.base import ContentFile
from django.urls import reverse


def generate_vehicle_verification_qr(vehicle, request=None):
    """
    Generate QR code for vehicle verification.
    
    Args:
        vehicle: Vehicle instance
        request: Optional Django request object to build absolute URL dynamically
    """
    if request:
        # Build absolute URL using the current request's host
        relative_url = reverse("parking:vehicle_verify", 
                             kwargs={"token": vehicle.verification_token})
        url = request.build_absolute_uri(relative_url)
    else:
        # Fallback to model method (uses BASE_URL setting)
        url = vehicle.get_verification_url()
    
    qr_image = qrcode.make(url)
    image_buffer = BytesIO()
    qr_image.save(image_buffer, format="PNG")
    return ContentFile(
        image_buffer.getvalue(),
        name=f"vehicle-verification-{vehicle.verification_token}.png",
    )


def generate_permit_verification_qr(permit, request=None):
    """
    Generate QR code for parking permit verification.
    
    Args:
        permit: ParkingPermit instance
        request: Optional Django request object to build absolute URL dynamically
    """
    if request:
        # Build absolute URL using the current request's host
        relative_url = reverse("parking:permit-verify", 
                             kwargs={"qr_token": permit.qr_token})
        url = request.build_absolute_uri(relative_url)
    else:
        # Fallback to model method (uses BASE_URL setting)
        url = permit.get_verification_url()
    
    qr_image = qrcode.make(url)
    image_buffer = BytesIO()
    qr_image.save(image_buffer, format="PNG")
    return ContentFile(
        image_buffer.getvalue(),
        name=f"parking-permit-verification-{permit.qr_token}.png",
    )
