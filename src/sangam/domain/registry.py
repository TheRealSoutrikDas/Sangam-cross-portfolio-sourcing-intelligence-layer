"""The canonical spec registry.

This is the asset. Everything else in the repo is machinery for populating it
and machinery for exploiting it. Thirty brands describing the same bottle
thirty ways is not a data-entry problem, it is the absence of a shared
referent, and this file is the shared referent.

In production this lives in Postgres, versioned and diffable, because "who
changed the definition of SPEC-CTN-KFT-350-ML and when" is a question that
gets asked after a bad delivery.
"""
from .models import CanonicalSpec, Vendor

SPECS: list[CanonicalSpec] = [
    CanonicalSpec(
        spec_id="SPEC-GLS-AMB-050-N18",
        family="primary_glass",
        label="Amber glass bottle, 50 ml, 18 mm neck",
        attrs={"material": "glass", "shade": "amber", "volume_ml": 50, "neck_mm": 18},
    ),
    CanonicalSpec(
        spec_id="SPEC-CAP-ALU-018",
        family="closure",
        label="Aluminium screw cap, 18 mm, EPE liner",
        attrs={"material": "aluminium", "type": "screw_cap", "neck_mm": 18, "liner": True},
    ),
    CanonicalSpec(
        spec_id="SPEC-CTN-KFT-350-ML",
        family="secondary_carton",
        label="Kraft mono carton, 350 gsm, 4-colour, matt lamination",
        attrs={"material": "kraft", "gsm": 350, "colours": 4, "finish": "matt_lam"},
    ),
    CanonicalSpec(
        spec_id="SPEC-JAR-PET-200-N70",
        family="primary_plastic",
        label="PET jar, 200 ml, white opaque, 70 mm neck",
        attrs={"material": "pet", "colour": "white_opaque", "volume_ml": 200, "neck_mm": 70},
    ),
    CanonicalSpec(
        spec_id="SPEC-PCH-LAM-250",
        family="flexible",
        label="Laminated stand-up pouch, 250 g",
        attrs={"material": "laminate", "format": "standup_pouch", "fill_g": 250},
    ),
]

SPEC_BY_ID = {s.spec_id: s for s in SPECS}


VENDORS: list[Vendor] = [
    Vendor(vendor_id="V-VIDHATA", name="Vidhata Glass Works",
           location="Firozabad, UP", origin="domestic"),
    Vendor(vendor_id="V-SHAKTI", name="Shakti Packaging",
           location="Vapi, GJ", origin="domestic"),
    Vendor(vendor_id="V-OMPRINT", name="Om Print & Pack",
           location="Sivakasi, TN", origin="domestic"),
    Vendor(vendor_id="V-MERIDIAN", name="Meridian Polymers",
           location="Daman", origin="domestic"),
    Vendor(vendor_id="V-SUNRISE", name="Sunrise Packaging Co.",
           location="Guangzhou, CN", origin="import",
           inland_freight_inr_per_pc=0.15, duty_pct=0.10),
    # Supplies Sattva Wellness today. Nothing on file. The system finding this
    # is arguably worth more than the price optimisation.
    Vendor(vendor_id="V-KRISHNA", name="Krishna Glass Udyog",
           location="Firozabad, UP", origin="domestic",
           inland_freight_inr_per_pc=0.40),
]

VENDOR_BY_ID = {v.vendor_id: v for v in VENDORS}
VENDOR_BY_NAME = {v.name: v for v in VENDORS}
