#!/usr/bin/env python3
"""
Catálogo nacional de municipios para "la ciudad" (fork MX, 2026-11-22).

Junta, una sola vez y sin IA, todo lo que hace falta para crear el territorio de cualquier ciudad de México:
  - INEGI Censo 2020, ITER nacional: por municipio, edad x sexo (18+), religión, escolaridad (15+/18+), lengua indígena,
    coordenadas de la cabecera y las localidades más pobladas (sirven de "colonia" fuera de la ciudad central);
  - Metrópolis de México 2020 (SEDATU/CONAPO/INEGI): a qué zona metropolitana, metrópoli municipal o zona conurbada
    pertenece cada municipio (datos/metropolis_2020.json, validado: 92 metrópolis, 421 municipios, 82.5 M hab.);
  - AMAI NSE 2022: distribución por área metropolitana (13) o, si no hay, por entidad (32).

Salida: datos/catalogo_municipios.json (lo carga `citizen_creator catalogo` en Postgres).
Uso: python3 -I tools/catalogo_municipios.py
"""
import csv
import json
import os
import re
import unicodedata

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'datos')
ITER = os.path.join(BASE, 'fuentes/inegi_iter_nacional/iter_00_cpv2020/conjunto_de_datos/conjunto_de_datos_iter_00CSV20.csv')
TOP_LOCALIDADES = 40
AGE_COLS = {
    '18-24': ['P_18A24'], '25-34': ['P_25A29', 'P_30A34'], '35-44': ['P_35A39', 'P_40A44'],
    '45-54': ['P_45A49', 'P_50A54'], '55-64': ['P_55A59', 'P_60A64'],
    '65+': ['P_65A69', 'P_70A74', 'P_75A79', 'P_80A84', 'P_85YMAS'],
}


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0  # INEGI usa "*" (confidencial) y "N/D"


def coord(s):
    m = re.match(r"\s*(\d+)°(\d+)'([\d.]+)\"\s*([NSEW])", s or '')
    if not m:
        return None
    v = int(m[1]) + int(m[2]) / 60 + float(m[3]) / 3600
    return round(-v if m[4] in 'SW' else v, 5)


def norm(x):
    return ''.join(ch for ch in unicodedata.normalize('NFD', x.lower()) if unicodedata.category(ch) != 'Mn')


def main():
    zonas = json.load(open(os.path.join(BASE, 'metropolis_2020.json'), encoding='utf-8'))
    amai = json.load(open(os.path.join(BASE, 'amai_nse_2022.json'), encoding='utf-8'))
    amai_metro = {norm(k): v for k, v in amai['por_area_metropolitana'].items()}
    zona_de = {}
    for z in zonas:
        for m in z['municipios']:
            zona_de[m['cve']] = z

    mun, locs = {}, {}
    with open(ITER, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            ent, mu, loc = r['ENTIDAD'], r['MUN'], r['LOC']
            if mu == '000':
                continue
            cve = ent + mu
            if loc == '0000':
                mun[cve] = r
            elif loc == '0001':
                mun.setdefault('_cab', {})[cve] = (coord(r['LATITUD']), coord(r['LONGITUD']))
            if loc not in ('0000', '9998', '9999'):
                locs.setdefault(cve, []).append((num(r['POBTOT']), r['NOM_LOC']))
    cab = mun.pop('_cab')

    out = []
    for cve, r in sorted(mun.items()):
        p18 = num(r['P_18YMAS']) or 1
        edad_sexo = [{'grupo': g, 'hombres': int(sum(num(r[c + '_M']) for c in cols)),
                      'mujeres': int(sum(num(r[c + '_F']) for c in cols))} for g, cols in AGE_COLS.items()]
        p15 = num(r['P_15YMAS']) or 1
        sin_esc = 100 * num(r['P15YM_SE']) / p15
        posbas = 100 * num(r['P18YM_PB']) / p18
        escolaridad = {'sin_escolaridad': round(sin_esc, 2), 'basica': round(max(0.0, 100 - sin_esc - posbas), 2),
                       'posbasica': round(posbas, 2)}
        pob = num(r['POBTOT']) or 1
        religion = {'catolica': round(100 * num(r['PCATOLICA']) / pob, 2),
                    'protestante_evangelica': round(100 * num(r['PRO_CRIEVA']) / pob, 2),
                    'otra': round(100 * num(r['POTRAS_REL']) / pob, 2),
                    'sin_religion': round(100 * num(r['PSIN_RELIG']) / pob, 2)}
        z = zona_de.get(cve)
        nse_src, nse = 'entidad', amai['por_entidad'].get(r['NOM_ENT'])
        if z and norm(z['nombre']) in amai_metro:
            nse_src, nse = 'area_metropolitana', amai_metro[norm(z['nombre'])]
        if nse is None:  # nombres de entidad que no coinciden exacto (p. ej. "Coahuila de Zaragoza")
            for k, v in amai['por_entidad'].items():
                if norm(k) in norm(r['NOM_ENT']) or norm(r['NOM_ENT']) in norm(k):
                    nse = v
                    break
        top = [n for _, n in sorted(locs.get(cve, []), reverse=True)[:TOP_LOCALIDADES]]
        lat, lon = cab.get(cve, (None, None))
        out.append({
            'cve': cve, 'nombre': r['NOM_MUN'], 'estado': r['NOM_ENT'], 'lat': lat, 'lon': lon,
            'poblacion': int(pob), 'pob_18mas': int(p18),
            'zona': {'id': z['id'], 'tipo': z['tipo'], 'nombre': z['nombre']} if z else None,
            'demografia': {
                'edad_sexo': edad_sexo, 'escolaridad': escolaridad, 'religion': religion,
                'hablantes_lengua_indigena_pct': round(100 * num(r['P3YM_HLI']) / (num(r['P_3YMAS']) or 1), 2),
                'nse_amai': nse, 'nse_fuente': nse_src, 'localidades': top,
            },
        })
    dest = os.path.join(BASE, 'catalogo_municipios.json')
    json.dump({'fuentes': {
        'censo': 'INEGI Censo de Población y Vivienda 2020, ITER nacional (iter_00_cpv2020_csv.zip)',
        'metropolis': 'SEDATU/CONAPO/INEGI, Metrópolis de México 2020 (MM2020_06022024.pdf)',
        'nse': amai['fuente'],
        'escolaridad': 'sin_escolaridad = P15YM_SE/P_15YMAS; posbasica = P18YM_PB/P_18YMAS (bachillerato + superior, '
                       'el ITER no los separa); basica = resto'},
        'municipios': out}, open(dest, 'w', encoding='utf-8'), ensure_ascii=False)
    sin_nse = [m['cve'] for m in out if not m['demografia']['nse_amai']]
    sin_coord = [m['cve'] for m in out if m['lat'] is None]
    print(f"{len(out)} municipios -> {dest} ({os.path.getsize(dest) // 1024} KB); sin NSE: {len(sin_nse)}; "
          f"sin coordenadas: {len(sin_coord)}; en metrópolis: {sum(1 for m in out if m['zona'])}")


if __name__ == '__main__':
    main()
