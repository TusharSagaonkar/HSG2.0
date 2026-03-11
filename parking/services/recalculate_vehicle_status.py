from parking.services.recalculate_vehicle_rule_status import recalculate_vehicle_rule_status


def recalculate_vehicle_status(society_id, **kwargs):
    del kwargs
    return recalculate_vehicle_rule_status(society_id)
