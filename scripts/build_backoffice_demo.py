"""Demo data for the back-office (web/admin.html): organisations, platform users with roles per module, beneficiaries.

Everything here is SYNTHETIC (demo: true): the beneficiaries are the growers of the DEMO vineyard register
(web/data/register.geojson) with invented names, their parcels and the DEMO AIPA applications of web/data/compliance.json;
people, IDNP / IDNO, phones and e-mails are generated. Institution names are real (the intended users of the platform),
nothing else about them is.

Usage:  python scripts/build_backoffice_demo.py   ->  web/data/backoffice.json
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

WEB = C.ROOT / "web" / "data"
R = random.Random(2026)

MODULES = [
    {"id": "control", "name": "Control conformitate", "roles": [
        ["inspector", "Inspector", ["control:view", "control:route", "pv:create", "target:check", "cereri:view", "cadastru:search"]],
        ["inspector_sef", "Inspector-șef", ["control:view", "control:assign", "control:route", "pv:create", "pv:approve", "cereri:view", "cereri:assign"]]]},
    {"id": "registru", "name": "Registrul vitivinicol", "roles": [
        ["operator_rvv", "Operator registru", ["rvv:view", "rvv:edit", "rvv:export", "cadastru:search"]],
        ["analist_rvv", "Analist", ["rvv:view", "rvv:export", "rapoarte:view"]]]},
    {"id": "subventii", "name": "Subvenții AIPA", "roles": [
        ["evaluator", "Evaluator cereri", ["cereri:view", "cereri:evaluate", "cereri:stage", "beneficiari:view"]],
        ["aprobator", "Aprobator plăți", ["cereri:view", "cereri:decide", "plati:approve", "beneficiari:view"]]]},
    {"id": "plantatie", "name": "Plantație", "roles": [
        ["admin_exploatatie", "Administrator exploatație", ["plantatie:view", "plantatie:route", "cereri:submit", "rapoarte:own"]]]},
    {"id": "rapoarte", "name": "Rapoarte", "roles": [["analist", "Analist rapoarte", ["rapoarte:view", "rapoarte:export"]]]},
    {"id": "backoffice", "name": "Back-office", "roles": [
        ["sysadmin", "Administrator de sistem", ["users:manage", "orgs:manage", "roles:manage", "beneficiari:manage", "cereri:manage", "audit:view"]],
        ["admin_modul", "Administrator modul", ["users:view", "roles:assign", "audit:view"]]]},
]
PERMS = {   # key: (title, description, type)
    "control:view": ("Vizualizare control", "Vede blocurile, conformitatea cu registrul și cererile de verificat", "Modul"),
    "control:route": ("Traseu de control", "Calculează traseul de control în portal", "Modul"),
    "control:assign": ("Alocare controale", "Împarte controalele pe teren între inspectori", "Modul"),
    "pv:create": ("Proces-verbal", "Întocmește procesul-verbal de control", "Modul"),
    "pv:approve": ("Aprobare proces-verbal", "Aprobă procesele-verbale ale inspectorilor", "Modul"),
    "target:check": ("Ținte verificate", "Marchează țintele verificate pe teren", "Modul"),
    "cereri:view": ("Vizualizare cereri", "Vede cererile de subvenție", "Modul"),
    "cereri:assign": ("Alocare cereri", "Alocă inspectorul unei cereri", "Modul"),
    "cereri:evaluate": ("Evaluare cereri", "Verifică dosarul și stabilește suma eligibilă", "Modul"),
    "cereri:stage": ("Schimbare etapă", "Trece cererea la etapa următoare", "Modul"),
    "cereri:decide": ("Decizie", "Aprobă sau respinge cererea", "Modul"),
    "cereri:submit": ("Depunere cereri", "Depune cereri de subvenție prin portal", "Modul"),
    "cereri:manage": ("Administrare cereri", "Acces complet la cereri", "Modul"),
    "plati:approve": ("Aprobare plăți", "Aprobă plata subvenției", "Modul"),
    "beneficiari:view": ("Vizualizare beneficiari", "Vede exploatațiile și parcelele lor", "Modul"),
    "beneficiari:manage": ("Administrare beneficiari", "Adaugă și editează beneficiari", "Modul"),
    "rvv:view": ("Registrul viticol", "Vede înregistrările din registrul viticol", "Modul"),
    "rvv:edit": ("Editare registru", "Modifică înregistrările din registru", "Modul"),
    "rvv:export": ("Extras din registru", "Exportă extrase din registru", "Modul"),
    "cadastru:search": ("Căutare în cadastru", "Caută parcele în cadastrul național", "Global"),
    "plantatie:view": ("Plantația", "Vede starea blocurilor proprii", "Modul"),
    "plantatie:route": ("Traseu de colectare", "Calculează traseul de colectare a deșeurilor", "Modul"),
    "rapoarte:view": ("Rapoarte", "Vede rapoartele", "Global"),
    "rapoarte:export": ("Export rapoarte", "Exportă rapoartele în CSV", "Global"),
    "rapoarte:own": ("Rapoarte proprii", "Rapoartele propriei exploatații", "Modul"),
    "users:view": ("Vizualizare utilizatori", "Vede utilizatorii platformei", "Global"),
    "users:manage": ("Administrare utilizatori", "Creează, editează și dezactivează utilizatori", "Global"),
    "orgs:manage": ("Administrare organizații", "Creează și editează organizații", "Global"),
    "roles:manage": ("Administrare roluri", "Creează roluri și permisiuni", "Global"),
    "roles:assign": ("Atribuire roluri", "Atribuie roluri utilizatorilor", "Global"),
    "audit:view": ("Jurnal de activitate", "Vede activitatea utilizatorilor", "Global"),
}
ROLE_INFO = {   # role key: (description, sign-in only with MPass)
    "control:inspector": ("Controlul pe teren al plantațiilor și al cererilor de subvenție", True),
    "control:inspector_sef": ("Coordonează inspectorii subdiviziunii teritoriale", True),
    "registru:operator_rvv": ("Ține la zi Registrul vitivinicol", True),
    "registru:analist_rvv": ("Analizează datele registrului", False),
    "subventii:evaluator": ("Evaluează dosarele cererilor de subvenție", True),
    "subventii:aprobator": ("Decide și aprobă plățile", True),
    "plantatie:admin_exploatatie": ("Administrează plantația proprie și depune cereri", False),
    "rapoarte:analist": ("Vede și exportă rapoartele", False),
    "backoffice:sysadmin": ("Administrează platforma: utilizatori, organizații, roluri", True),
    "backoffice:admin_modul": ("Atribuie roluri într-un modul", True),
}
LOC_STRASENI = ["Strășeni", "Bucovăț", "Sireți", "Cojușna", "Codreanca", "Dolna", "Lozova", "Micăuți", "Romănești", "Scoreni",
                "Voinova", "Vorniceni", "Zubrești"]


def orgs():
    o = [("ORG-001", "MAIA", "Ministerul Agriculturii și Industriei Alimentare", None, "Autoritate centrală", "mun. Chișinău", "Chișinău"),
         ("ORG-002", "AIPA", "Agenția de Intervenție și Plăți pentru Agricultură", "ORG-001", "Agenție", "mun. Chișinău", "Chișinău"),
         ("ORG-003", "ONVV", "Oficiul Național al Viei și Vinului", "ORG-001", "Agenție", "mun. Chișinău", "Chișinău"),
         ("ORG-004", "ONVV · Registrul vitivinicol", "Departamentul care ține Registrul vitivinicol", "ORG-003", "Subdiviziune", "mun. Chișinău", "Chișinău")]
    for i, r in enumerate(["Strășeni", "Ialoveni", "Orhei", "Hîncești", "Călărași", "Cahul", "Cimișlia", "Ștefan Vodă", "Soroca", "Bălți"]):
        raion = "mun. Bălți" if r == "Bălți" else f"r-nul {r}"
        o.append((f"ORG-{5 + i:03d}", f"AIPA {r}", f"Subdiviziunea teritorială {r} a AIPA", "ORG-002", "Subdiviziune teritorială", raion, r))
    o += [("ORG-015", "Primăria Sireți", "Autoritatea publică locală a comunei Sireți", None, "Autoritate locală", "r-nul Strășeni", "Sireți"),
          ("ORG-016", "VinePlan · operator platformă", "Echipa tehnică a platformei (demo)", None, "Operator platformă", "mun. Chișinău", "Chișinău")]
    by = {x[0]: x for x in o}

    def path(i):
        p, x = [], by[i]
        while x:
            p.append(x[1]); x = by.get(x[3])
        return " / ".join(reversed(p))
    return [{"id": i, "titlu": t, "descriere": d, "parinte": p, "tip": tip, "nume_ierarhic": path(i), "raion": ra, "localitate": lo,
             "strada": "", "nr": "", "bloc": "", "apartament": "", "statut": "Activ", "creat": f"2026-0{R.randint(1, 8)}-{R.randint(10, 28)}", "demo": True}
            for i, t, d, p, tip, ra, lo in o]


def idnp(year):
    return f"2{year % 100:02d}{R.randint(0, 9)}{R.randint(10 ** 8, 10 ** 9 - 1)}"


def phone():
    return f"+373 6{R.randint(0, 9)} {R.randint(100, 999)} {R.randint(100, 999)}"


def users(org_ids):
    rows = [  # prenume, nume, sex, org, titlu, roles, statut
        ("Administrator de sistem", "demo", "M", "ORG-016", "Administrator de sistem", ["backoffice:sysadmin"], "Activ"),
        ("Inspector", "demo", "M", "ORG-005", "Inspector principal", ["control:inspector", "rapoarte:analist"], "Activ"),
        ("Administrator", "demo", "F", None, "Administrator exploatație", ["plantatie:admin_exploatatie"], "Activ"),
        ("Ion", "Rusu", "M", "ORG-005", "Șef subdiviziune", ["control:inspector_sef", "rapoarte:analist"], "Activ"),
        ("Elena", "Ceban", "F", "ORG-002", "Specialist principal", ["subventii:evaluator"], "Activ"),
        ("Mihai", "Popa", "M", "ORG-002", "Șef direcție plăți", ["subventii:aprobator", "rapoarte:analist"], "Activ"),
        ("Ana", "Munteanu", "F", "ORG-004", "Specialist registru", ["registru:operator_rvv"], "Activ"),
        ("Vasile", "Lungu", "M", "ORG-003", "Consultant", ["registru:analist_rvv", "rapoarte:analist"], "Activ"),
        ("Natalia", "Rotaru", "F", "ORG-001", "Consultant principal", ["rapoarte:analist"], "Activ"),
        ("Sergiu", "Cojocaru", "M", "ORG-006", "Inspector superior", ["control:inspector"], "Activ"),
        ("Tatiana", "Botnaru", "F", "ORG-007", "Inspector", ["control:inspector"], "Activ"),
        ("Andrei", "Ciobanu", "M", "ORG-008", "Inspector", ["control:inspector"], "Activ"),
        ("Olga", "Melnic", "F", "ORG-010", "Inspector superior", ["control:inspector"], "Activ"),
        ("Dumitru", "Sârbu", "M", "ORG-009", "Inspector", ["control:inspector"], "Activ"),
        ("Irina", "Guțu", "F", "ORG-015", "Specialist în agricultură", ["rapoarte:analist"], "Activ"),
        ("Victor", "Cazacu", "M", "ORG-016", "Administrator modul", ["backoffice:admin_modul", "rapoarte:analist"], "Activ"),
        ("Maria", "Vlas", "F", "ORG-013", "Inspector", ["control:inspector"], "Inactiv"),
        ("Petru", "Țurcanu", "M", "ORG-014", "Inspector", ["control:inspector"], "Blocat"),
        ("Ludmila", "Ursu", "F", "ORG-004", "Specialist registru", ["registru:operator_rvv", "registru:analist_rvv"], "Activ"),
        ("Nicolae", "Grosu", "M", "ORG-011", "Specialist", ["subventii:evaluator"], "Activ"),
        ("Cristina", "Railean", "F", "ORG-012", "Inspector", ["control:inspector"], "În așteptare"),
        ("Alexandru", "Moraru", "M", "ORG-005", "Inspector", ["control:inspector"], "Activ"),
    ]
    out = []
    for k, (pre, nume, sex, org, titlu, roles, st) in enumerate(rows, 1):
        year = R.randint(1968, 1998)
        demo_acc = {("Administrator de sistem", "demo"): "backoffice@fieldplanner.demo", ("Inspector", "demo"): "inspector@fieldplanner.demo",
                    ("Administrator", "demo"): "administrator@fieldplanner.demo"}.get((pre, nume))
        email = demo_acc or f"{pre.lower()}.{nume.lower()}@exemplu.md".translate(str.maketrans("ăâîșțş", "aaistt"))
        out.append({"id": f"USR-{k:03d}", "prenume": pre, "nume": nume, "idnp": idnp(year), "telefon": phone(), "email": email,
                    "titlu": titlu, "data_nasterii": f"{year}-{R.randint(1, 12):02d}-{R.randint(1, 28):02d}", "sex": sex,
                    "organizatie": org, "beneficiar": "BEN-001" if (pre, nume) == ("Administrator", "demo") else None,
                    "roluri": roles, "statut": st, "raion": "r-nul Strășeni" if org in ("ORG-005", "ORG-015") else "mun. Chișinău",
                    "localitate": "Strășeni" if org == "ORG-005" else "Sireți" if org == "ORG-015" else "Chișinău",
                    "strada": "", "nr": "", "bloc": "", "apartament": "", "creat": f"2026-0{R.randint(1, 9)}-{R.randint(10, 28)}", "demo": True})
    return out


NAMES = {"DEMO-G-02": ("Vie Sireți SRL", "SRL"), "DEMO-G-04": ("Podgoria Codrului SRL", "SRL"), "DEMO-G-05": ("GȚ Moraru Ion", "GȚ"),
         "DEMO-G-09": ("Dealul Sireților SRL", "SRL"), "DEMO-G-12": ("Codru-Vin SA", "SA"), "DEMO-G-14": ("Agro-Vita Strășeni SRL", "SRL"),
         "DEMO-G-16": ("GȚ Ceban Vasile", "GȚ"), "DEMO-G-21": ("ÎI Botnaru Andrei", "ÎI"), "DEMO-G-22": ("Terasa Verde SRL", "SRL"),
         "DEMO-G-32": ("GȚ Rusu Maria", "GȚ"), "DEMO-G-33": ("Moșia Codru SRL", "SRL"), "DEMO-G-37": ("GȚ Lungu Petru", "GȚ"),
         "DEMO-G-41": ("Vinăria Sireți-Deal SRL", "SRL"), "DEMO-G-42": ("GȚ Guțu Tudor", "GȚ"), "DEMO-G-44": ("GȚ Cojocaru Ion", "GȚ"),
         "DEMO-G-45": ("Viile Bîcului SRL", "SRL"), "DEMO-G-93": ("GȚ Popa Elena", "GȚ"), "DEMO-G-94": ("GȚ Sârbu Mihail", "GȚ")}
FORMA = {"SRL": "Societate cu răspundere limitată", "SA": "Societate pe acțiuni", "GȚ": "Gospodărie țărănească (de fermier)",
         "ÎI": "Întreprindere individuală"}
REPR = ["Andrei Rusu", "Elena Moraru", "Ion Moraru", "Vasile Ceban", "Tudor Guțu", "Maria Rusu", "Petru Lungu", "Andrei Botnaru", "Ion Cojocaru",
        "Victoria Popa", "Sergiu Munteanu", "Natalia Grosu", "Mihail Sârbu", "Ana Railean", "Dorin Melnic", "Liliana Țurcanu", "Oleg Vlas", "Diana Cazacu"]
STREETS = ["str. Viilor", "str. Principală", "str. Codrului", "str. Livezilor", "str. Ștefan cel Mare", "str. Mihai Eminescu", "str. Podgoriei"]


def beneficiaries():
    reg = json.loads((WEB / "register.geojson").read_text())["features"]
    cmp_ = {b["vineyard_id"]: b for b in json.loads((WEB / "compliance.json").read_text())["blocks"]}
    rank = {"neconform": 0, "verificare": 1, "conform": 2, "sub_prag": 3}
    growers = {}
    for f in reg:
        p = f["properties"]; growers.setdefault(p["grower_id"], []).append(p)
    out = []
    for k, (gid, ps) in enumerate(sorted(growers.items(), key=lambda kv: (NAMES[kv[0]][0] != "Vie Sireți SRL", kv[0])), 1):
        name, forma = NAMES[gid]
        parcels, req, st = [], [], None
        for p in ps:
            m = p.get("measured") or {}
            parcels.append({"cad_nr": p["cad_nr"], "vineyard_id": p.get("vineyard_id"), "rvv_code": p.get("rvv_code"), "soi": p.get("variety"),
                            "an_plantare": p.get("planting_year"), "suprafata_declarata_ha": p.get("declared_area_ha"),
                            "suprafata_masurata_ha": m.get("measured_area_ha"), "diferenta_pct": m.get("area_diff_pct"), "statut": p.get("status"),
                            "statut_detectat": m.get("status_detected"), "igp": p.get("pdo_pgi"), "autorizatie": p.get("authorisation_nr"),
                            "ultimul_control": max((e["date"] for e in p.get("events", []) if e["type"] == "inspection"), default=None)})
            b = cmp_.get(p.get("vineyard_id"))
            if b:
                st = b["status"] if st is None or rank[b["status"]] < rank[st] else st
                e, a = b.get("eligibility"), (b.get("registry") or {}).get("aipa_request")
                if e and a and not any(r['vineyard_id'] == b['vineyard_id'] for r in req):
                    status = "Respinsă (neeligibilă)" if e["eligible_lei"] == 0 else "Aprobată" if e["eligible_lei"] >= e["requested_lei"] else "Eligibilă parțial"
                    req.append({"id": f"CER-{a['year']}-{len(req) + 1:02d}{k:02d}", "masura": "SP 2.5 · înființarea plantațiilor viticole", "masura_completa": e["measure"],
                                "an": a["year"], "vineyard_id": b["vineyard_id"], "suma_ceruta_lei": e["requested_lei"], "suma_eligibila_lei": e["eligible_lei"],
                                "statut": status, "depusa": f"{a['year']}-0{R.randint(3, 6)}-{R.randint(10, 28)}"})
        if not req and R.random() < 0.3 and parcels[0].get("vineyard_id"):        # a few applications still under evaluation
            req.append({"id": f"CER-2026-{k:02d}01", "masura": "SP 2.5 · înființarea plantațiilor viticole", "an": 2026, "vineyard_id": parcels[0]["vineyard_id"],
                        "suma_ceruta_lei": R.choice([18000, 24500, 31000, 39500]), "suma_eligibila_lei": None, "statut": "În evaluare",
                        "depusa": f"2026-0{R.randint(4, 8)}-{R.randint(10, 28)}"})
        idno = f"100{R.randint(10 ** 9, 10 ** 10 - 1)}"
        rep = REPR[(k - 1) % len(REPR)]
        out.append({"id": f"BEN-{k:03d}", "denumire": name, "forma": forma, "forma_completa": FORMA[forma], "idno": idno, "cod_exploatant": gid,
                    "reprezentant": rep, "telefon": phone(), "email": f"contact@{name.split()[0].lower().translate(str.maketrans('ăâîșțş', 'aaistt'))}.exemplu.md",
                    "raion": "r-nul Strășeni", "localitate": "Sireți" if k % 4 else R.choice(LOC_STRASENI), "strada": R.choice(STREETS), "nr": str(R.randint(1, 120)),
                    "bloc": "", "apartament": "", "suprafata_declarata_ha": round(sum(p["suprafata_declarata_ha"] or 0 for p in parcels), 3),
                    "suprafata_masurata_ha": round(sum(p["suprafata_masurata_ha"] or 0 for p in parcels), 3), "parcele": parcels, "cereri": req,
                    "conformitate": st or ("fara_bloc" if not any(p["vineyard_id"] for p in parcels) else "sub_prag"),
                    "statut": "Suspendat" if gid == "DEMO-G-94" else "Activ", "inregistrat": f"20{R.randint(18, 25)}-0{R.randint(1, 9)}-{R.randint(10, 28)}", "demo": True})
    return out


STAGES = ["Depunere", "Verificare administrativă", "Control pe teren", "Evaluare", "Decizie", "Plată"]
UA = ["Chrome 129 · Windows 11", "Edge 129 · Windows 11", "Chrome 129 · macOS", "Safari 18 · iOS 18", "Chrome 129 · Android 14", "Firefox 131 · Windows 10"]
CTX = {}


def ctx_of(user):
    """Where a user works from: an IP of the documentation ranges (RFC 5737, never a real address), a browser, the sign-in method."""
    if user is None:
        return {"ip": f"203.0.113.{R.randint(10, 250)}", "ua": R.choice(UA), "auth": "MPass"}
    if user not in CTX:
        CTX[user] = {"ip": f"198.51.100.{R.randint(10, 250)}", "ua": R.choice(UA), "auth": R.choice(["MPass", "MPass", "Parolă"])}
    return dict(CTX[user])


def cereri(bens, users_):
    """Every beneficiary with a block has one AIPA application (SP 2.5); the 4 of compliance.json keep their amounts.
    Stage, the inspector assigned (AIPA Strășeni) and the field-control result follow the drone compliance of the block."""
    cmp_ = {b["vineyard_id"]: b for b in json.loads((WEB / "compliance.json").read_text())["blocks"]}
    uid = {f"{u['prenume']} {u['nume']}": u["id"] for u in users_}
    insp = [uid["Inspector demo"], uid["Alexandru Moraru"]]
    chief, evalr, appr = uid["Ion Rusu"], uid["Elena Ceban"], uid["Mihai Popa"]
    out, n = [], 0
    for b in bens:
        vids = [p["vineyard_id"] for p in b["parcele"] if p["vineyard_id"]]
        if not vids:
            continue
        n += 1
        vid = vids[0]; blk = cmp_.get(vid, {}); st = blk.get("status", "sub_prag")
        old = b["cereri"][0] if b["cereri"] else None
        req = old["suma_ceruta_lei"] if old else R.choice([18000, 21500, 24500, 31000, 39500, 46000])
        if old and old["suma_eligibila_lei"] is not None:
            elig = old["suma_eligibila_lei"]
            stage, dec = ("Decizie", old["statut"]) if elig < req else ("Plată", "Aprobată")
        elif st == "neconform":
            stage, dec, elig = "Evaluare", None, None
        elif st == "verificare":
            stage, dec, elig = "Control pe teren", None, None
        elif st == "conform":
            stage, dec, elig = R.choice([("Verificare administrativă", None, None), ("Control pe teren", None, None)])
        else:
            stage, dec, elig = "Verificare administrativă", None, None
        year = old["an"] if old else 2026
        dep = old["depusa"] if old else f"2026-0{R.randint(6, 8)}-{R.randint(10, 28)}"
        inspector = insp[n % 2] if STAGES.index(stage) >= 2 else None
        result = {"neconform": "neconform", "verificare": "de verificat", "conform": "conform"}.get(st) if STAGES.index(stage) >= 3 else None
        cid = f"CER-{year}-{n:03d}"
        ev, t = [], __import__("datetime").datetime.fromisoformat(dep + "T09:12")
        def add(days, user, act, **kw):
            nonlocal t
            t = t + __import__("datetime").timedelta(days=days, minutes=R.randint(3, 400))
            ev.append({"ts": t.isoformat(timespec="minutes"), "user": user, "action": act, "module": "Cereri de subvenție",
                       "stage": kw.pop("stage", None), **ctx_of(user), **kw})
        add(0, None, "creare", what="Cerere depusă prin portal", by=b["reprezentant"], stage="Depunere")
        if STAGES.index(stage) >= 1:
            add(2, evalr, "vizualizare", what="Dosarul cererii")
            add(1, evalr, "modificare", field="Etapa", before="Depunere", after="Verificare administrativă")
        if STAGES.index(stage) >= 2:
            add(3, evalr, "modificare", field="Etapa", before="Verificare administrativă", after="Control pe teren")
            add(0, chief, "modificare", field="Inspector alocat", before="—", after=inspector)
            add(1, inspector, "vizualizare", what=f"Blocul {vid} pe hartă (declarat vs măsurat)")
            add(0, inspector, "traseu", what=f"Traseu de control prin blocul {vid}")
        if STAGES.index(stage) >= 3:
            add(2, inspector, "atasament", what=f"Proces-verbal de control · bloc {vid}")
            add(0, inspector, "modificare", field="Rezultat control", before="—", after=result)
            add(0, inspector, "modificare", field="Etapa", before="Control pe teren", after="Evaluare")
        if STAGES.index(stage) >= 4:
            add(3, evalr, "modificare", field="Suma eligibilă (lei)", before="—", after=f"{elig:,}".replace(",", " "))
            add(1, appr, "modificare", field="Etapa", before="Evaluare", after="Decizie")
            add(0, appr, "modificare", field="Decizie", before="—", after=dec)
        if stage == "Plată":
            add(5, appr, "modificare", field="Etapa", before="Decizie", after="Plată")
        docs = [["Extras din Registrul vitivinicol", "depus"], ["Numerele cadastrale ale parcelelor", "depus"], ["Proiectul de înființare a plantației", "depus"],
                ["Certificatul de categorie biologică a materialului săditor", "depus"], ["Certificatul de membru IGP / DOP", "depus" if STAGES.index(stage) >= 2 or R.random() < 0.7 else "lipsă"],   # an incomplete file never leaves the administrative check
                ["Actul de recepție a plantației (prindere ≥ 90 %)", "depus" if STAGES.index(stage) >= 3 else "în așteptare"]]
        if STAGES.index(stage) <= 1 and R.random() < 0.5:
            docs[3][1] = "lipsă"                     # an incomplete file still in the administrative check
        out.append({"id": cid, "beneficiar": b["id"], "masura": "SP 2.5 · înființarea plantațiilor viticole", "documente": docs,
                    "masura_completa": "SP_2.5 Investiții în exploatațiile din sectorul viticol: înființarea plantațiilor (struguri pentru vin)",
                    "an": year, "vineyard_id": vid, "parcele": [p["cad_nr"] for p in b["parcele"] if p["vineyard_id"] == vid],
                    "suma_ceruta_lei": req, "suma_eligibila_lei": elig, "etapa": stage, "decizie": dec, "inspector": inspector,
                    "rezultat_control": result, "conformitate_bloc": st, "depusa": dep, "activitate": ev, "demo": True})
        b["cereri"] = [cid]
    for b in bens:
        if b["cereri"] and isinstance(b["cereri"][0], dict):
            b["cereri"] = []
    return out


def sessions(users_, cer):
    """Sign-in, sign-out and report exports around each user's work on the applications (what an audit log also keeps)."""
    import datetime as dt
    days = {}
    for c in cer:
        for e in c["activitate"]:
            if e.get("user"):
                days.setdefault(e["user"], set()).add(e["ts"][:10])
    out = []
    reports = ["Raport cereri pe etape", "Raport controale pe teren", "Extras din registrul viticol", "Conformitate pe blocuri"]
    for u in users_:
        if u["statut"] != "Activ":
            continue
        ds = sorted(days.get(u["id"], set()) | {f"2026-09-{R.randint(18, 26):02d}"})
        for d in ds[-6:]:
            c = ctx_of(u["id"])
            t0 = dt.datetime.fromisoformat(d + f"T0{R.randint(7, 9)}:{R.randint(10, 59)}")
            out.append({"ts": t0.isoformat(timespec="minutes"), "user": u["id"], "action": "conectare", "module": "Autentificare", "what": "Conectare", **c})
            if R.random() < 0.35:
                out.append({"ts": (t0 + dt.timedelta(minutes=R.randint(20, 200))).isoformat(timespec="minutes"), "user": u["id"], "action": "export",
                            "module": "Rapoarte", "title": R.choice(reports), "what": "A exportat raportul în CSV", **c})
            out.append({"ts": (t0 + dt.timedelta(hours=R.randint(4, 9))).isoformat(timespec="minutes"), "user": u["id"], "action": "deconectare",
                        "module": "Autentificare", "what": "Deconectare", **c})
    return out


def main() -> None:
    o = orgs()
    us, bens = users([x["id"] for x in o]), beneficiaries()
    cer = cereri(bens, us)
    ses = sessions(us, cer)
    data = {"about": "DATE DEMONSTRATIVE (sintetice) pentru back-office: persoane, IDNP/IDNO, telefoane, e-mailuri, beneficiari, cereri, activitate. "
                     "Numele instituțiilor sunt reale (utilizatorii vizați ai platformei); restul nu.",
            "demo": True, "modules": MODULES, "etape": STAGES, "organizatii": o, "utilizatori": us, "beneficiari": bens, "cereri": cer, "sesiuni": ses,
            "permisiuni": [{"id": k, "cheie": k, "titlu": v[0], "descriere": v[1], "tip": v[2], "demo": True} for k, v in PERMS.items()],
            "roluri": [{"id": f"{m['id']}:{r[0]}", "cheie": f"{m['id']}:{r[0]}", "titlu": r[1], "descriere": ROLE_INFO[f"{m['id']}:{r[0]}"][0],
                        "modul": m["id"], "mpass": ROLE_INFO[f"{m['id']}:{r[0]}"][1], "permisiuni": r[2], "demo": True} for m in MODULES for r in m["roles"]],
            "raioane": [f["properties"]["name"] for f in json.loads((WEB / "moldova.geojson").read_text())["features"] if f["properties"].get("kind") == "raion"],
            "localitati": {"r-nul Strășeni": LOC_STRASENI}}
    (WEB / "backoffice.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"backoffice.json: {len(data['organizatii'])} organizations, {len(data['utilizatori'])} users, {len(data['beneficiari'])} beneficiaries, "
          f"{len(data['cereri'])} applications ({sum(len(c['activitate']) for c in data['cereri'])} activity events), "
          f"{sum(len(b['parcele']) for b in data['beneficiari'])} parcels")


if __name__ == "__main__":
    main()
