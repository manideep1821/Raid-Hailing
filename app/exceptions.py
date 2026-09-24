class RideHailingError(Exception):
    """Base class for domain errors."""


class NotFoundError(RideHailingError):
    pass


class NoDriverAvailableError(RideHailingError):
    pass


class InvalidCouponError(RideHailingError):
    pass


class InvalidRideStateError(RideHailingError):
    pass


class ValidationError(RideHailingError):
    pass
