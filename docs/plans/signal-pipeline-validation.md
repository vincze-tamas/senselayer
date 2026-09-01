# SenseLayer – kétfázisú Signal Pipeline Validation terv

**Státusz:** review-ra kész, implementáció még nem engedélyezett  
**Dátum:** 2026-09-01  
**Repository:** `vincze-tamas/senselayer`  
**Kiinduló commit:** `1fd3a5cca99491b857b5dd0633a1700a982cccd7`  
**Előző review:** `CHANGES_REQUIRED` – a stratégiai irány elfogadva, a production szerződések és acceptance részletei hiányosak voltak.

## 1. Döntés

A személyes baseline Milestone 3 előtt validálni kell az EEG feature pipeline-t. Ezt azonban **nem production infrastruktúra építésével kezdjük**.

A munka két, külön engedélyezendő fázisra bomlik:

1. **Fázis A – lokális offline bizonyítás:** rövid raw Muse corpus, replay, szintetikus scorer, DSP bake-off és held-out fizikai acceptance. Nincs VPS-upload, új receiver API, production adatbázis-migráció vagy dashboard-cutover.
2. **Fázis B – production integráció:** csak sikeres Fázis A után készül végrehajtható terv a clock contractra, v2 feature sémára, raw lifecycle-ra, shadow rolloutra és rollbackre.

Ez a szétválasztás megőrzi a tudományos szigort, de nem építünk termék-infrastruktúrát egy még ki nem választott algoritmus köré.

## 2. Miért szükséges a Fázis A?

A jelenlegi collector:

- 2 másodperces ablakból egyetlen periodogramot számol;
- csak átlaglevonást és Hann-ablakot használ;
- a négy csatorna sávenergiájának mediánját egyetlen értékké vonja össze;
- az öt sávot 1-re normalizálja;
- nem ismer külön slow-drift, pislogás- vagy szemmozgás-flaget;
- eldobja az LSL timestampet;
- raw EEG-t nem tárol.

Ezért a magas delta lehet valódi alacsony frekvenciás aktivitás, de lehet drift vagy okuláris műtermék is. Baseline csak akkor építhető, ha ezek a hatások reprodukálhatóan elkülöníthetők.

## 3. Fázis A – scope és biztonsági korlát

### Benne van

- új, különálló lokális validációs capture eszköz;
- lokális raw adatbázis és manifest;
- szintetikus és valós raw replay;
- több preprocessing/PSD jelölt összehasonlítása;
- artefaktum-gating prototípus;
- development és külön held-out valós Muse mérés;
- automatikus összehasonlító riport;
- baseline GO/NO-GO döntés.

### Nincs benne

- a production collector, receiver, SQLite history vagy dashboard működésének megváltoztatása;
- raw upload a VPS-re;
- v2 receiver/storage schema;
- Focus/Relaxation vagy neurofeedback;
- folyamatos raw archiválás;
- production cutover vagy deployment.

**Fail-safe:** a Fázis A új eszközei nem írhatják a production `history.db`-t, nem POSTolhatnak a receivernek, és nem módosíthatják a futó Windows scheduled taskot.

## 4. Fázis A – rögzített technikai szerződések

### 4.1. Lokális raw formátum

A validációs corpus első formátuma **külön SQLite adatbázis WAL módban**. Ez nem production raw-formátum-döntés.

Indok:

- Python standard library; nincs `pyarrow` függőség;
- append és tranzakció támogatás;
- crash után olvasható marad;
- rövid, 5–10 perces corpushoz elegendő;
- sample és marker ugyanazon lokális adatbázisban atomikusan köthető a capture UUID-hoz.

Táblák:

```text
captures(capture_id, created_at, protocol_version, source_name,
         declared_sample_rate, channel_order_json, software_commit,
         dependency_snapshot_json, status, notes)

segments(segment_id, capture_id, sequence_no, started_edge_wall,
         started_edge_monotonic_ns, lsl_time_correction, reconnect_reason)

raw_samples(capture_id, segment_id, sample_index, lsl_timestamp,
            edge_wall_timestamp, edge_monotonic_ns,
            tp9_uv, af7_uv, af8_uv, tp10_uv)

markers(capture_id, segment_id, marker_id, label,
        edge_wall_timestamp, edge_monotonic_ns, notes)
```

Követelmények:

- explicit TP9/AF7/AF8/TP10 csatornasorrend;
- `float64` nyers érték;
- capture lezárásakor SHA-256 checksum és manifest;
- félbeszakadt capture `interrupted`, nem `completed` státuszt kap;
- a fájl alapértelmezetten helyi marad és kézzel törölhető.

### 4.2. Időalap

A Fázis A minden mintát és markert ugyanazon Windows edge gépen rögzít.

- `lsl_timestamp`: stream-idő és gap-elemzés;
- `edge_monotonic_ns`: marker–minta illesztés elsődleges időalapja;
- `edge_wall_timestamp`: emberileg olvasható idő;
- `lsl_time_correction`: rögzített diagnosztikai mező;
- minden reconnect új `segment_id`;
- segmenthatáron átívelő ablak automatikusan invalid;
- reconnect és filter-reset után konfigurált warm-up idő invalid.

VPS-óra és szerveroldali marker ebben a fázisban nem vesz részt.

### 4.3. Ablak-szerződés

- A feature timestampje az elemzési ablak **vége**.
- Az ablak nem nyúlhat át segment- vagy protocol-block határon.
- Átlógás esetén az ablak `boundary_guard` miatt invalid.
- Live és replay ugyanazt a tiszta processing függvényt használja.
- A replay chunkolása nem változtathatja a window boundaries, flag-ek vagy validity eredményét.

### 4.4. Determinizmus és tolerancia

Az előző `1e-9` globális abszolút tolerancia törölve.

Követelmény:

- azonos manifest, dependency snapshot és window boundaries;
- flag-ek, validity és sávbesorolás pontos egyezése;
- relatív power: `rtol=1e-7`, `atol=1e-9` azonos platformon;
- abszolút PSD: `rtol=1e-6`, skálafüggő `atol`;
- külön Windows/Linux cross-platform riport;
- byteazonosság csak a kanonikus JSON/CSV serializationnél követelhető.

## 5. Előre rögzített validációs adatkészletek

### 5.1. Szintetikus development fixture

Legalább:

- tiszta 2, 6, 10, 20 és 35 Hz jel;
- két vagy három sávból álló kevert spektrum;
- alacsony SNR + broadband noise;
- sávhatár-közeli frekvenciák;
- alpha + lineáris és polinomiális drift;
- alpha + blink tranziens;
- beta + muscle burst;
- 50 Hz hálózati komponens;
- amplitude scaling;
- gap, flatline, abrupt step és channel outlier.

A fixture generátora seedelt, paraméterezett és verziózott.

### 5.2. Valós development corpus

Egy felhelyezéssel, egy napon:

- 3 × 60 s nyitott szem, fix pont;
- 3 × 60 s csukott szem;
- 30 s normál pislogás;
- 30 s vezényelt pislogás;
- 30 s állkapocsfeszítés;
- 20 s fejpántérintés;
- 20 s egy elektróda meglazítása;
- 30 s helyreállás.

Ez használható paraméterválasztásra, de nem végső acceptance-re.

### 5.3. Held-out acceptance corpus

Másik napon vagy legalább új felhelyezéssel, ugyanazon előre rögzített protokollal készül. A DSP- és detector-paraméterek a capture megkezdése előtt freeze-eltek. A held-out eredmény után tuning csak új verzióval és új acceptance capture-rel lehetséges.

## 6. DSP bake-off

### 6.1. Jelöltek

**Kontroll:** jelenlegi 2 s periodogram.

**Welch-jelöltek:**

- rolling 8 s elemzési ablak;
- 4 s szegmens;
- 50% overlap;
- Hann window;
- median averaging;
- 1 s feature-frissítés.

Összehasonlítandó preprocessing:

- high-pass: 0,5 Hz és 1,0 Hz;
- detrend: constant és linear;
- 50 Hz notch: csak külön jelölt, nem automatikus alapértelmezés.

Ha az 1 Hz high-pass nyer, a 0,5–1 Hz tartomány nem nevezhető teljes delta mérésnek; az output elnevezését ehhez kell igazítani.

### 6.2. Scorer – előre rögzített metrikák

| Metrika | Számítás | Szerep |
|---|---|---|
| Frequency accuracy | ismert domináns sáv helyes felismerése | hard gate |
| Boundary leakage | szomszédos sávba jutó energia | rangsorolás |
| Drift leakage | drift hozzáadása utáni delta-infláció | hard gate |
| Amplitude scaling error | 2× amplitúdó → 4× abszolút power eltérése | hard gate |
| Clean CV | nem átfedő clean epochok coefficient of variation értéke | rangsorolás |
| Artifact sensitivity | helyesen rejectált címkézett artifact epochok | hard gate |
| Clean specificity | helyesen megtartott clean epochok | hard gate |
| Transition latency | állapotváltás és stabil feature közti idő | rangsorolás |
| Valid coverage | clean blokkok valid aránya | hard gate |
| Runtime | CPU-idő, memória, real-time factor | hard gate |
| Warm-up loss | reset után elvesző idő | rangsorolás |

### 6.3. Hard gate küszöbök

Ezek a Fázis A előzetes mérnöki küszöbei; Hermes review során csak indokolt módosítással változhatnak, még implementáció előtt.

- középsávos tiszta/kevert fixture domináns sáv felismerése: 100%;
- amplitude scaling relatív hiba: legfeljebb 10%;
- drift fixture esetén a cél-sáv dominanciája megmarad, delta növekedése legfeljebb 0,15 relatív power;
- held-out artifact sensitivity: legalább 0,80;
- held-out clean specificity: legalább 0,80;
- held-out clean valid coverage: legalább 0,70;
- real-time factor: legfeljebb 0,25 a cél Windows gépen;
- reconnect vagy gapet átfedő ablak: 0% téves validálás.

Az értékeléshez **nem átfedő epochokat** kell használni. Osztályonként legalább 20 értékelhető epoch szükséges; az eredmény confusion matrixszal és elemszámmal jelenik meg.

### 6.4. Győztes kiválasztása

1. Bármely hard gate bukása kizárás.
2. A túlélő jelöltek Pareto-összehasonlítása: drift leakage, clean CV, transition latency, runtime, warm-up loss.
3. Holtversenyben az egyszerűbb, kevesebb késleltetésű konfiguráció nyer.
4. A held-out corpus nem használható kiválasztásra; csak a freeze-elt győztes elfogadására vagy elutasítására.

## 7. Artefaktum-gating prototípus

Fázis A-ban detektálunk és elutasítunk, nem „javítunk”.

Flag-ek:

- `slow_drift`;
- `blink_or_eye_movement`;
- `muscle_activity`;
- `line_noise_50hz`;
- meglévő `flatline`, `extreme_amplitude`, `abrupt_steps`, `high_frequency_noise`, `channel_outlier`;
- `acquisition_gap`, `boundary_guard`, `filter_warmup`.

Korlát: külön EOG/EMG nélkül a blink és muscle heurisztikus címke. A confusion matrix a **protokoll szerint kiváltott eseményt** méri, nem klinikai ground truth-t.

## 8. Végrehajtható kártyabontás – Fázis A

### A0 – Contract és executable acceptance

**Fájlok:**

- `docs/plans/signal-pipeline-validation.md` – jelen terv;
- `docs/ROADMAP.md` – plan-only validation gate Milestone 2 és 3 közé;
- `README.md` – link és státusz;
- `CHANGELOG.md` – `Unreleased`, plan only.

**Eredmény:** scope, clock, raw schema, scorer, held-out szabály és hard gate freeze-elt.

### A1 – Szintetikus fixture és scorer

**Tervezett fájlok:** `validation/fixtures.py`, `validation/scorer.py`, `validation/contracts.py`, `tests/test_validation_fixtures.py`, `tests/test_validation_scorer.py`.

**Parancs:** `python -m pytest tests/test_validation_fixtures.py tests/test_validation_scorer.py -q`

**Eredmény:** DSP implementáció nélkül futó, reprodukálható acceptance harness.

### A2 – Bounded lokális capture és minimális protocol runner

**Tervezett fájlok:** `scripts/capture_validation_raw.py`, `validation/raw_store.py`, `validation/protocol.py`, `tests/test_validation_raw_store.py`, `tests/test_validation_protocol.py`.

**Parancs:** `python -m pytest tests/test_validation_raw_store.py tests/test_validation_protocol.py -q`

**Eredmény:** lokális SQLite capture, automatikus block/marker vezérlés, checksum és manifest. Nincs production adatút.

### A3 – Replay és timestamp/gap integritás

**Tervezett fájlok:** `validation/replay.py`, `validation/timing.py`, `tests/test_validation_replay.py`, `tests/test_validation_timing.py`.

**Parancs:** `python -m pytest tests/test_validation_replay.py tests/test_validation_timing.py -q`

**Eredmény:** fix window boundaries, segment/gap/boundary/warm-up gating és determinisztikus replay.

### A4 – DSP-jelöltek és bake-off

**Tervezett fájlok:** `validation/dsp_candidates.py`, `validation/bakeoff.py`, `requirements-validation.txt`, `tests/test_validation_dsp.py`, `tests/test_validation_bakeoff.py`.

**Parancs:** `python -m pytest tests/test_validation_dsp.py tests/test_validation_bakeoff.py -q`

**Eredmény:** géppel olvasható és Markdown összehasonlító riport, kizárt jelöltek indoklásával.

### A5 – Artefaktum-gating prototípus

**Tervezett fájlok:** `validation/artifacts.py`, `tests/test_validation_artifacts.py`.

**Parancs:** `python -m pytest tests/test_validation_artifacts.py -q`

**Eredmény:** development corpuson freeze-elt detektor és dokumentált thresholdok.

### A6 – Held-out valós Muse acceptance

**Tervezett output:** `validation_reports/<capture-id>/manifest.json`, `report.md`, confusion matrix, per-channel táblák, kontroll-vs-győztes összehasonlítás és GO/NO-GO jegyzőkönyv.

**Eredmény:** a held-out corpus egyszer fut le a freeze-elt konfiguráción. Bukás esetén nincs baseline és nincs production integráció.

## 9. Fázis A Definition of Done

- új kód csak `validation/`, dedikált script és tesztterületen;
- production collector/receiver/storage/dashboard diffje nulla;
- minden célzott és teljes regressziós teszt átmegy;
- secret scan és diff review átmegy;
- development és held-out corpus külön capture ID;
- hard gate-ek géppel ellenőrzöttek;
- riport tartalmazza a dependency snapshotot és commit SHA-t;
- baseline GO/NO-GO döntés dokumentált;
- nincs deployment vagy production cutover.

## 10. Fázis B – csak GO után tervezhető

A Fázis B jelenleg **nem implementációs scope**. GO esetén külön terv kötelező legalább ezekkel:

- raw capture control plane és bounded artifact lifecycle;
- LSL/edge/server clock-domain contract;
- v1 backward compatibility és pontos v2 JSON schema;
- per-channel absolute/relative feature storage külön verziózott táblában;
- idempotens migráció és rollback utáni olvashatóság;
- v1/v2 shadow számítás ugyanazon ablakon;
- pipeline version/config hash;
- quality-gated dashboard és gapszakítás;
- offline buffer/upload retry/checksum;
- cutover flag, edge rollback és adatbázis-backup;
- külön production Muse acceptance.

Fázis B végén, sikeres shadow rollout után indulhat a személyes medián/MAD baseline.

## 11. GO / NO-GO

### GO a production integráció tervezésére

- minden Fázis A hard gate teljesül;
- a held-out eyes-closed blokkokban a TP9/TP10 alpha a három párosításból legalább kettőben magasabb a szomszédos eyes-open blokknál;
- a freeze-elt győztes a jelenlegi kontrollnál kisebb drift leakage-et ad elfogadható latency mellett;
- nincs timestamp-, gap- vagy segment-integritási bizonytalanság;
- az eredmény második replay során reprodukálható.

### NO-GO

- drift továbbra is clean delta-dominanciát okoz;
- artifact és clean epochok nem különíthetők el a hard gate szerint;
- a held-out eredmény tuning nélkül nem ismétli a development eredményt;
- replay/live windowing vagy timestamp illesztés eltér;
- a Muse 2 négy csatornája a kívánt use case-hez nem ad stabil feature-t.

NO-GO esetén nem épül production v2 és baseline. A riportnak ki kell mondania, hogy algoritmus-, protokoll- vagy eszközkorlát okozta-e a bukást.

## 12. Mélykutatási döntés

Most nem teljes deep research a következő lépés. A döntő bizonyíték a saját Muse 2 development és held-out raw corpuson futó, előre rögzített scorer. Célzott szakirodalmi ellenőrzés a hard gate-ek és a végleges production DSP-konfiguráció freeze-elése előtt szükséges.

## 13. Hermes következő review-jának kérdései

Hermes ne implementáljon. A review kizárólag ezt döntse el:

1. A Fázis A valóban leválik-e teljesen a production adatútról?
2. A lokális SQLite raw contract és edge-időalap végrehajtható-e a jelenlegi Windows/MuseLSL környezetben?
3. A scorer metrikái és hard gate-jei előre rögzítettek és nem túlilleszthetők-e?
4. A development/held-out szétválasztás megfelelő-e?
5. Az A0–A6 fájl-, teszt- és parancsszintű bontás kódolható-e további architekturális döntés nélkül?
6. Maradt-e blocker a Fázis A implementációjának engedélyezése előtt?
