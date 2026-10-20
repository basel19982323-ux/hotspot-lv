"""Hourly load models for one house: base (hushållsel), heat pump, EV charging, PV.

Every model returns kW per hour on the study-year UTC index (8760 rows).
Positive = consumption, negative = generation. Import the submodules directly:

    from hotspotlv.loads.base import base_load_profiles
    from hotspotlv.loads.heatpump import heat_pump_profile, HeatPumpParams
    from hotspotlv.loads.ev import ev_charging_profile, EVParams
    from hotspotlv.loads.pv import pv_profile, draw_kwp
"""
