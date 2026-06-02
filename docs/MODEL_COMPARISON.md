# ESFEX frente a modelos de referencia del sector — comparación exhaustiva

*Preparado 2026-06-01. Los datos de ESFEX están anclados en el árbol fuente `esfex`
(README + inspección de código); los datos de los comparadores fueron verificados contra
documentación oficial, GitHub/PyPI/Zenodo y artículos fuente (estado a inicios de 2026).
Las afirmaciones aproximadas o sujetas a verificación van marcadas.*

---

## 0. TL;DR — dónde se sitúa ESFEX

ESFEX **no compite de frente con ningún modelo concreto de la lista**. Es un
**framework híbrido Python+Julia de grado investigación (autor único)** cuya apuesta
distintiva es la **amplitud de formulaciones de flujo de potencia** — 6 formulaciones,
incluyendo las relajaciones convexas SOCP/QC/SDP que normalmente solo aparecen en
`PowerModels.jl` — integradas con expansión de capacidad, despacho operativo,
acoplamiento sectorial (hidrógeno/calor), variantes estocásticas/robustas, métricas de
flexibilidad, un módulo financiero (TIR/VAN), MGA/SPORES y una GUI de escritorio.

- Sus **pares funcionales** son las herramientas open-source de planificación/despacho:
  **PyPSA, GenX, Switch, Calliope**.
- Su **par en ambición de flujo de potencia** es **PowerModels.jl** (la única
  herramienta de uso amplio que ofrece la misma familia AC/DC/SOCP/QC/SDP) — no estaba
  en la lista del usuario pero es el comparador más relevante para esa característica.
- Las **herramientas comerciales** (PLEXOS, PSS/E, Aurora, PROMOD, GE-MAPS) y los
  **ESOM/IAM de sistema energético completo** (TIMES, MESSAGEix, OSeMOSYS) operan a una
  escala, madurez y validación que un código de etapa doctoral no iguala — pero tampoco
  ofrecen el menú de relajaciones convexas de OPF ni el enfoque en métricas de
  flexibilidad de ESFEX.

Resumen honesto: **ESFEX cambia madurez y escala probada por un conjunto de
funcionalidades inusualmente amplio e integrado, orientado a investigación.**

---

## 1. Qué es ESFEX realmente (anclado en el código)

| Atributo | ESFEX (`esfex`) |
|---|---|
| Tipo | **Expansión de capacidad + despacho operativo + OPF multi-formulación**, enfoque en flexibilidad |
| Autor / madurez | Autor único (M. Soto Calvo, trabajo doctoral); licencia **MIT**; estado **alfa** (~v0.1.x); ~155k líneas Python + ~18k Julia; 53 archivos de test, validación IEEE |
| **Arquitectura** | **Híbrida: Python (`esfex`) = CLI, configuración, I/O, GUI, orquestación; Julia (JuMP) = modelos de optimización**. Puente `juliacall` (PythonCall.jl) |
| Clases de problema | LP / MILP (despacho, UC, expansión) y NLP / QCP / cónico (AC-OPF, SOCP/SDP/QC) |
| Solvers | **HiGHS (por defecto)**; Gurobi/CPLEX/Xpress/SCIP/CBC/GLPK (LP/MILP); **Ipopt** (NLP); SCS/Clarabel/MOSEK (cónico) |
| **Formulaciones de flujo (6, vía `power_flow_mode`)** | `dcopf` (DC-OPF base de ciclos + pérdidas PWL); `dcopf_ac_verify` (DC + verificación AC Newton-Raphson); `acopf_soc` (relajación cónica de 2º orden); `acopf_qc` (relajación cuadrática-convexa + McCormick); `acopf_sdp` (relajación semidefinida — *bloqueada por escalado numérico*); `acopf_polar` (NLP exacto V,θ); `acopf_rect` (NLP exacto Vr,Vi) |
| Descomposición | **Master MILP** (inversión/retiro/expansión, horizonte 25 años) + **Operacional LP/NLP** (despacho a nivel de bus) + **Primary Energy** (cadenas de H2/combustibles) + **horizonte rodante** (ventanas de 2 semanas con solape) |
| Temporal | Horario (config. 1h/6h/24h); días representativos (~5/año por clustering de pico); planificación multi-año con **previsión perfecta** (también modo miope/estocástico opcional) |
| Espacial | Distinción **nodo** (región) vs **bus** (eléctrico). Master a nivel de nodo (copperplate), operación a nivel de bus. Caso Cuba: **10 nodos, 417 buses, 579 ramas** |
| Acoplamiento sectorial | Electrolizadores (power-to-H2), cadena de suministro de hidrógeno/amoníaco/synfuels (Primary Energy), calor (caldera + bomba de calor; no totalmente acoplado) |
| Reservas / SS.AA. | Reserva estática (spinning) + dinámica (5–30 min) + **inercia** (constante H); **N-1 de generación y transmisión como restricción blanda penalizada** |
| Capa de flexibilidad | Métricas dedicadas: rampa, ciclado de almacenamiento, curtailment, interconexión, índice compuesto |
| Capa financiera | CAPEX/OPEX, **retiro por VAN** (iterativo: unidades con VAN<0 se fuerzan a retiro), LCOE/VALLCOE, depreciación |
| MGA / SPORES | Sí — alternativas casi-óptimas diversas dentro de una banda de holgura |
| Módulos de datos avanzados | EV/V2G, solar de tejado, demanda ML (TFT/XGBoost/PyTorch), perfiles climáticos CMIP6, evaluación de peligros/curvas de fragilidad, análisis de centros de datos |
| Nicho | **Estudio OTEC/OTEX** (siting de energía oceanotérmica) — ausente en todas las demás |
| I/O | Entrada: **YAML** (config), **CSV/Excel** (series); Salida: **HDF5** (primario), CSV, Excel, JSON |
| GUI | **PySide6/Qt**: editor de red (drag-drop, Leaflet), editor de config, visor de resultados HDF5-nativo, diagrama unifilar |

**Comprobación de realidad (memoria de proyecto):** el runner puede emitir HDF5
disperso/sucio (reservas a cero, desglose de costes vacío, flujo espurio en nodos
aislados). Es decir, la *superficie de capacidades* es amplia, pero el *endurecimiento de
producción* sigue en curso — el encuadre correcto para cualquier comparación.

---

## 2. El panorama de comparadores (3 familias)

Las herramientas nombradas abarcan tres mundos distintos; compararlas en un solo eje
sería engañoso, así que se agrupan:

- **A. Planificación/despacho open-source de sistemas de potencia** — PyPSA, GenX,
  Switch, Calliope *(+ PowerModels.jl para OPF)*. **← grupo par real de ESFEX.**
- **B. ESOM/IAM de sistema energético completo (baja resolución espacio-temporal,
  multi-década)** — OSeMOSYS, TIMES/MARKAL, MESSAGEix, Temoa.
- **C. Herramientas comerciales / de grado industrial** — PLEXOS, Aurora, PROMOD,
  GE-MAPS/MARS, PSS/E *(+ Antares, open-source pero de grado TSO)*.

---

## 3. Matriz maestra de comparación

Leyenda: ✔ = nativo/fuerte · ◑ = parcial/vía extensión · ✘ = no soportado.

| Dimensión | **ESFEX** | PyPSA | GenX | Switch | Calliope | OSeMOSYS | TIMES | MESSAGEix | Temoa | PLEXOS | Antares | PSS/E |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Familia | A | A | A | A | A | B | B | B | B | C | C | C |
| Licencia | Apache-2.0 | MIT | GPL-2.0 | Apache-2.0 | Apache-2.0 | Apache-2.0 | GPLv3 modelo / **GAMS pagado** | Apache-2.0 / **GAMS pagado** | MIT | **comercial** | **MPLv2 (open)** | **comercial** |
| Lenguaje | **Python+Julia** | Python | **Julia** | Python | Python | MathProg/Py/GAMS | **GAMS** | GAMS+Python | Python | C++/.NET | C++ | Fortran/Py |
| Capa opt. | **JuMP** (+orq. Python) | **linopy** | JuMP | Pyomo | Pyomo (+Gurobi) | GLPK/Pyomo/PuLP | GAMS | GAMS/ixmp | Pyomo | Xpress/Gurobi | Sirius/OR-Tools | numérico |
| Solver por defecto | **HiGHS** | HiGHS | HiGHS | GLPK | CBC | GLPK | CPLEX | CPLEX | HiGHS | Gurobi | Sirius | — |
| Expansión capacidad | ✔ multi-año | ✔ multi-periodo | ✔ (núcleo) | ✔ multi-periodo | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ◑ (Xpansion) | ✘ |
| Despacho / PCM | ✔ | ✔ | ✔ | ✔ | ◑ | ◑ slices | ◑ slices | ◑ slices | ◑ slices | ✔ | ✔ | ✘ |
| Unit commitment (MILP) | ✔ | ✔ | ✔ (entero/agrupado) | ✔ | ◑ (units enteras) | ✘ | ◑ add-on | ✘ | ✘ | ✔ SCUC | ◑ | ✘ |
| **DC-OPF (física KVL)** | ✔ | ✔ (LOPF ciclos) | ◑ (opcional) | ◑ transporte | ✘ transporte | ✘ | ◑ add-on PTDF | ✘ | ✘ | ✔ | ◑ (DC/NTC) | ✔ |
| **AC-OPF completo (NLP)** | **✔ polar+rect** | ◑ solo *power flow* N-R | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✔ (+dinámica) |
| **Relajaciones convexas SOCP/QC/SDP** | **✔ (las 3)** | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ |
| Transporte / NTC | ✔ | ✔ (Links) | ✔ | ✔ | ✔ | ◑ | ◑ | ◑ | ◑ | ✔ | ✔ | n/a |
| Almacenamiento | ✔ | ✔ | ✔ (LDES) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✘ |
| Reservas / SS.AA. | ✔ (+inercia) | ✔ | ✔ | ✔ | ◑ | ✘ | ◑ | ✘ | ◑ margen | ✔ | ◑ | ✘ |
| Acoplamiento sectorial | ✔ (H2/calor) | ✔ (PyPSA-Eur-Sec) | ◑ (DOLPHYN) | ◑ | ✔ | ✔ completo | ✔ completo | ✔ completo | ✔ completo | ✔ (gas/agua/H2) | ◑ | ✘ |
| **Estocástico** | ✔ (ACOPF estoc.) | ✔ (2-etapas+CVaR) | ◑ escenarios | ✔ (PySP) | ✘ (rama exp.) | ◑ (PuLP) | ✔ nativo | ◑ variante | **✔ nativo+MGA** | ✔ Monte Carlo | **✔ Monte Carlo** | ✘ |
| **Optimización robusta** | ✔ (ACOPF robusto) | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ◑ | ✘ | ✘ |
| Resolución temporal | horaria | horaria + rep. | rep. (TDR) | timepoints | flexible | **time slices** | **time slices** | **slices** | **slices** | sub-horaria | horaria crono | snapshot/din |
| Escala espacial | pequeña–media (límite AC) | **miles de buses** | zonal/nodal | zonal | flexible | regiones | regiones | 11–14 reg | regiones | **interconexión** | zonal | **miles buses** |
| GUI | ✔ (PySide6) | ✘ (scripting) | ✘ | ✘ | ✘ | ◑ (MoManI) | ✔ (VEDA) | ◑ | ◑ | ✔ rica | ✔ | ✔ rica |
| Métricas flexibilidad | **✔ dedicadas** | ◑ derivar | ◑ | ◑ | ◑ | ✘ | ✘ | ✘ | ✘ | ◑ | ◑ | ✘ |
| Financiero / TIR | ✔ (VAN+LCOE/VALLCOE) | ◑ | ◑ | ◑ | ◑ | ◑ | ✔ | ✔ | ◑ | ✔ | ◑ | ✘ |
| MGA / alternativas | ✔ (SPORES) | ◑ | ◑ | ✘ | ✘ | ✘ | ◑ | ✘ | **✔** | ✘ | ✘ | ✘ |
| Madurez / comunidad | **autor único, alfa** | muy grande | grande | media | media | grande (UN/edu) | muy grande (gob) | grande (IIASA/IPCC) | media (académica) | muy grande (industria) | grande (RTE/ENTSO-E) | muy grande (industria) |

---

## 4. Notas por herramienta (con datos verificados)

### A. Planificación/despacho open-source (grupo par de ESFEX)

**PyPSA / PyPSA-Eur** — TU Berlin (T. Brown et al.), MIT, Python sobre **linopy** (Pyomo
en desuso). Primera versión 2017; línea v1.x. Estándar de facto para estudios europeos.
Distingue dos regímenes: (i) **flujo de potencia AC no lineal completo (Newton-Raphson)**
solo para *simulación/verificación* post-óptimo; (ii) optimización **LOPF lineal (DC)** con
restricciones KVL+KCL vía formulación de **flujos de ciclo** (equivalente a B-θ/PTDF), más
**transporte/NTC controlable** vía componentes Link. **No hay AC-OPF ni relajaciones
convexas en la optimización.** Escala a miles de buses; UC MILP completo; multi-periodo
(miope y previsión perfecta); estocástico nativo de 2 etapas + CVaR. Acoplamiento
sectorial fuerte (PyPSA-Eur-Sec: calor, transporte, H2, gas, industria, CO2). Solo
scripting (Snakemake). Adopción ENTSO-E (Open-TYNDP), PyPSA-Earth. **vs ESFEX:** PyPSA
gana en madurez, escala, comunidad y realismo sectorial; el único filo de formulación de
ESFEX es el AC-OPF real + relajaciones convexas, que PyPSA evita deliberadamente.

**GenX (GenX.jl)** — Princeton ZERO Lab/MIT/NYU/Binghamton, **GPL-2.0**, Julia/JuMP, v0.4.5
(2025). Expansión + operaciones co-optimizadas con UC seleccionable por recurso (entero /
agrupado-linealizado / relajado), reservas operativas, reducción de dominio temporal,
multi-etapa (miope/previsión perfecta). Red: copperplate / **transporte zonal** / **DC-OPF
opcional** (ejemplo IEEE 9-bus) con pérdidas PWL; sin AC. Sin GUI. Fuerte adopción US
(Net-Zero America, REPEAT). **vs ESFEX:** GenX más validado/eficiente para CEP grande;
ESFEX aporta física AC y GUI.

**Switch (switch-model)** — Berkeley RAEL / U. Hawai'i (M. Fripp), **Apache-2.0**,
Python/Pyomo, v2.0.9 (2025). Expansión multi-periodo + despacho (miope/previsión perfecta),
reservas spinning/non-spinning, **estocástico (PySP)**, UC MILP opcional. Red zonal de
transporte (DC/AC opcional en investigación). Sin GUI. Insignia: SWITCH-Hawaii (100%
renovable 2045), WECC. **vs ESFEX:** más probado para horizontes largos; sin física AC.

**Calliope** — ETH Zürich/Imperial (Pfenninger, Pickering), **Apache-2.0**, Python/Pyomo
(+backend Gurobi en v0.7), estable 0.6.10 / 0.7.0 en pre-release. **Modelo de
transporte/flujo de mercancías puramente lineal — sin DC-OPF, AC-OPF ni KVL.** Resolución
temporal flexible; modos plan (previsión perfecta) / operate (horizonte rodante); units
enteras (MILP). Definición de modelo YAML+CSV, matemática personalizable. Sin GUI. **vs
ESFEX:** modelo de datos más limpio y flexible; ESFEX ofrece la física de red que
Calliope no tiene.

**PowerModels.jl** *(no nombrado, pero el par clave de OPF)* — LANL, Julia/JuMP, open. La
implementación de referencia de **exactamente la misma familia** que ESFEX: AC
(polar/rect), DC, SOCP, QC, SDP. Es *solo* una librería de OPF/red (sin expansión, sin GUI,
sin sectores). **vs ESFEX:** PowerModels es el patrón oro *para las formulaciones de OPF en
sí* y está mucho más validado; la contribución de ESFEX es envolver esa amplitud de
formulación dentro de un framework de planificación + flexibilidad + GUI. **Esta es la
comparación más importante para la novedad distintiva de ESFEX.**

### B. ESOM / IAM de sistema energético completo (problema distinto)

Todos: modelos LP bottom-up, ricos en tecnología, de mínimo coste, **previsión perfecta**,
multi-década, **sin flujo de potencia físico** (balance de energía / transporte
"copperplate"), con **time slices** sub-anuales (no despacho horario cronológico por
defecto). Por eso se acoplan ("soft-link") rutinariamente a modelos de potencia dedicados.

**OSeMOSYS** — KTH/UCL/UN-DESA/Climate Compatible Growth, **Apache-2.0** (única cadena de
herramientas totalmente libre: GLPK/CBC/HiGHS). MathProg/Pyomo/PuLP/GAMS. Time slices
configurables (OSeMOSYS Global 1–288/año, 2015–2100). GUI MoManI. Estocástico vía
OSeMOSYS_PuLP. Gran adopción en capacitación / Sur Global / UN.

**TIMES/MARKAL** — IEA-ETSAP, modelo **GPLv3 pero requiere GAMS propietario** + interfaz
VEDA (de pago). El más rico en tecnología y más adoptado (~70 países, ~200 equipos).
Slices jerárquicos; previsión perfecta / miope / estocástico nativo. **Add-on de red
reciente (v3.4+) con DC/PTDF opcional** — la excepción que confirma la regla. Global =
ETSAP-TIAM.

**MESSAGEix** — IIASA, **Apache-2.0** (núcleo GAMS + Python `message_ix`/ixmp), v3.11.1
(2025). El más orientado a evaluación integrada (GLOBIOM uso del suelo + MACRO economía;
escenarios IPCC/SSP). Resolución eléctrica/temporal más gruesa (regiones copperplate, a
menudo carga media anual); por eso se acopla a **PLEXOS-World**. 11–14 regiones globales.

**Temoa** — NC State/CMU, **MIT**, Python/Pyomo, v4.0.0 (2026). ESOM clase TIMES con dos
sellos: **programación estocástica nativa (árbol de escenarios)** y **MGA** para explorar
el espacio casi-óptimo. Sin UC, sin power flow, slices estacionales. Base del US Open
Energy Outlook.

**vs ESFEX:** responden una pregunta distinta (transición de economía completa a décadas,
no flexibilidad horaria con física de red). Mucha menor resolución espacio-temporal y sin
flujo de potencia — pero mucho mayor alcance sectorial y trayectoria de política pública.
El acoplamiento sectorial de ESFEX (Primary Energy/H2) apunta a su territorio pero a
resolución de sistema de potencia (horaria, con red).

### C. Comercial / grado industrial

> **Eje limpio:** las herramientas de mercado/PCM (PLEXOS, Aurora, PROMOD, GE-MAPS) y la
> open-source Antares usan **DC-OPF / transporte lineal, nunca AC completo**. Solo **PSS/E**
> (y GE PSLF) hacen **flujo de potencia AC y dinámica/estabilidad** reales.

**PLEXOS** — Energy Exemplar, comercial, PLEXOS 11 (2024–25) + Cloud. PCM + expansión +
mercado co-optimizando electricidad/gas/agua/H2. MILP/LP/QP (Gurobi/Xpress/CPLEX/Mosek).
**DC-OPF** integrado con UC, LMP nodal, contingencias N-x (SCUC), pérdidas MLF/lineal/
cuadrática/cúbica. **Sin AC-OPF, sin dinámica** (el marketing a veces dice "AC", pero la
implementación documentada es DC-OPF). Sub-horaria→LT, cronológico, Monte Carlo + opt.
estocástica. 10.000s de buses. 1.500+ usuarios en 400+ organizaciones, 62 países. **vs
ESFEX:** PLEXOS domina en escala/validación/soporte/realismo operativo; los únicos filos
conceptuales de ESFEX son la física AC/convexa, la transparencia open-source y el encuadre
de flexibilidad/financiero — y es gratis.

**Antares** — RTE, **open-source (MPLv2 desde v9.0, 2024)**, C++. Simulador secuencial
Monte Carlo de adecuación/mercado (UCED) para grandes redes interconectadas. **Aproximación
DC / transporte (NTC)** + generador de restricciones de Kirchhoff; sin AC, sin dinámica.
**Horario cronológico** (8760h = 52 sub-problemas semanales/año MC). Expansión vía
**Antares-Xpansion** (tipo Benders). LP (MILP planificado), Sirius/Xpress/SCIP/GLPK vía
OR-Tools. GUI. Referencia del informe de adecuación de RTE y del **ENTSO-E TYNDP**. **vs
ESFEX:** Antares es la herramienta open que más se parece a un PCM comercial en robustez;
es el comparador open-source más fuerte de grado TSO. ESFEX ofrece física AC nodal e
integración CEP que Antares maneja de otro modo (zonal + Xpansion separado).

**PSS/E** — Siemens, comercial, v36, desde 1976. Estándar de **ingeniería de transmisión**:
**flujo de potencia AC completo + dinámica/estabilidad transitoria**, cortocircuito, AC-OPF
(módulo opcional). Sin PCM de mercado, sin SCUC MILP, sin expansión. Escala
interconexión (decenas de miles de buses). GUI + scripting Python. **vs ESFEX:** propósito
distinto; PSS/E es la referencia AC validada pero no hace optimización de inversión ni
despacho de mercado. El AC-OPF de ESFEX es de optimización económica, no de
dinámica/estabilidad.

**Aurora / PROMOD / GE-MAPS-MARS** — PCM comerciales + previsión de precios/mercado.
*Aurora* (Energy Exemplar, ex-AURORAxmp/EPIS — **¡distinto de Aurora Energy Research!**):
SCUC/SCOPF nodal DC, LMP, expansión LTCE. *PROMOD* (Hitachi Energy): SCUC/SCED nodal/zonal
DC, ahora cloud. *GE-MAPS* (GE Vernova/PlanOS) = PCM nodal DC; *GE-MARS* = adecuación Monte
Carlo secuencial (LOLE/EUE); *PSLF* aparte = AC. Todos: MILP/LP, horario cronológico, GUI
rica, adopción ISO/RTO/utilities. **vs ESFEX:** misma historia que PLEXOS — madurez
industrial vs amplitud de investigación open de ESFEX.

---

## 5. Diferenciadores genuinos de ESFEX

1. **Menú de relajaciones convexas de OPF (SOCP/QC/SDP) dentro de un framework de
   planificación.** Ninguna otra herramienta de la lista lo hace; el único comparador es
   `PowerModels.jl` (que es solo-OPF). **Es la afirmación de novedad más fuerte de ESFEX.**
2. **AC-OPF completo (polar + rectangular) co-residente con DC, transporte y expansión** —
   la mayoría de herramientas de planificación se quedan deliberadamente en lo lineal;
   ESFEX permite cambiar de fidelidad con un parámetro.
3. **Capa de métricas de flexibilidad** (rampa, ciclado de almacenamiento, curtailment,
   interconexión, índice compuesto) como salida de primera clase.
4. **Variantes OPF estocástica + robusta** de fábrica (la robusta es rara incluso entre las
   comerciales).
5. **Módulo financiero con retiro por VAN y LCOE/VALLCOE**, distinguiendo retorno a nivel de
   sistema vs nueva inversión — poco común en herramientas open de planificación.
6. **GUI de escritorio** (editor de red + visor de resultados HDF5 + estudio OTEC) — la
   mayoría de herramientas open son solo-scripting.
7. **Acoplamiento sectorial** electricidad–H2–calor con electrolizadores y cadena de
   suministro de combustibles.
8. **Módulos de datos integrados**: demanda ML (TFT/XGBoost), clima CMIP6, EV/V2G, solar de
   tejado, evaluación de peligros/fragilidad, análisis de centros de datos.
9. **MGA/SPORES** integrado para exploración de alternativas casi-óptimas.
10. **Siting OTEC/OTEX** — ausente en todas las demás.

## 6. Limitaciones honestas (para un artículo justo)

- **Madurez / validación:** autor único, alfa, problemas conocidos de salida sucia; sin
  comunidad de usuarios ni benchmarking independiente. Los modelos consolidados tienen años
  de revisión por pares y uso operativo.
- **Escala:** las formulaciones AC/cónicas limitan el tamaño de red tratable;
  PyPSA/PLEXOS/PSS-E manejan escalas de interconexión que ESFEX no.
- **`acopf_sdp` bloqueado** por escalado numérico — la formulación más ambiciosa no es
  operativa hoy.
- **Sin benchmark publicado** frente a MATPOWER/PowerModels (corrección AC-OPF) ni frente a
  PLEXOS/PyPSA (despacho/CEP) — un artículo de comparación necesitaría exactamente esto.
- **Acoplamiento sectorial** más estrecho y menos validado que PyPSA-Eur-Sec o los ESOM/IAM.

## 7. Encuadre sugerido para el artículo

Posicionar ESFEX como un **"framework de sistema de potencia centrado en flexibilidad y de
fidelidad múltiple que unifica formulaciones OPF convexas/AC (al estilo PowerModels.jl) con
expansión de capacidad, acoplamiento multi-energía y métricas de flexibilidad en una única
herramienta open Python+Julia con GUI."** La novedad defendible es la *integración*: llevar
el espectro de formulaciones OPF (normalmente confinado a investigación de OPF) a un flujo
de trabajo de planificación + flexibilidad + financiero con GUI. Para sostener la
afirmación, añadir: (i) validación de AC-OPF frente a MATPOWER/PowerModels en casos estándar
(IEEE 14/30/118 — los tests IEEE ya existen en el repo), y (ii) comprobación cruzada de
despacho/CEP frente a PyPSA o GenX en un sistema pequeño compartido.

---

### Comparadores de referencia (fuentes verificadas)
- PyPSA — https://pypsa.org · PyPSA-Eur — https://pypsa-eur.readthedocs.io
- GenX — https://genxproject.github.io/GenX.jl · Switch — https://switch-model.org
- Calliope — https://www.callio.pe · PowerModels.jl — https://lanl-ansi.github.io/PowerModels.jl
- OSeMOSYS — https://osemosys.org · TIMES — https://iea-etsap.org · MESSAGEix — https://docs.messageix.org
- Temoa — https://temoacloud.com
- PLEXOS — https://www.energyexemplar.com/plexos · Antares — https://antares-simulator.org
- PSS/E — https://www.siemens.com/pss-software · Aurora — https://www.energyexemplar.com/aurora
- PROMOD — https://www.hitachienergy.com · GE-MAPS/MARS — https://www.gevernova.com
