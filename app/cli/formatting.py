from typing import Dict, List

from app.domain.models import Ride


def format_ride(r: Ride) -> str:
    car = r.assigned_car_type.value
    if r.upgraded:
        car += f" (upgraded from {r.requested_car_type.value}, billed as {r.requested_car_type.value})"
    lines = [f"ride {r.id} [{r.status.value}]  user={r.user_id}  driver={r.driver_id}  car={car}",
             f"  booked {r.booked_at:%Y-%m-%d %H:%M:%S}"]
    if r.picked_up_at:
        lines.append(f"  picked up {r.picked_up_at:%Y-%m-%d %H:%M:%S}")
    if r.surge_multiplier != 1:
        lines.append(f"  surge x{r.surge_multiplier:g} (locked at booking)")
    if r.coupon:
        lines.append(f"  coupon {r.coupon.code}")
    if r.fare:
        f = r.fare
        lines.append(f"  distance {r.distance_km:.2f} km  base fare ₹{f.base_fare:.2f}")
        if f.surge_multiplier != 1:
            lines.append(f"  with surge x{f.surge_multiplier:g}: ₹{f.surged_fare:.2f}")
        if f.discount:
            lines.append(f"  coupon discount -₹{f.discount:.2f}")
        lines.append(f"  total ₹{f.total:.2f}")
    if r.cancellation_fee is not None:
        lines.append(f"  cancellation fee ₹{r.cancellation_fee:.2f}")
    return "\n".join(lines)


def format_history(history: Dict[str, List[Ride]]) -> str:
    out = []
    for status, rides in history.items():
        out.append(f"{status.upper()} ({len(rides)})")
        out.extend(format_ride(r) for r in rides)
    return "\n".join(out)
