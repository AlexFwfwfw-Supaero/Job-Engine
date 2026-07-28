# CV Master Reference — Experience Bank

A superset of everything that can go on a CV. The live `base/cv.tex` carries only a
subset; pick entries and bullets from here per target job. Not every item helps every
application — that is the point of keeping this separate.

**How to use:** scan the tag index for the target job's keywords, pull the matching
entries, then choose the bullet variant (short / full) that fits the space.

**Status key:** `LIVE` = currently in base CV · `HELD` = written, not in base CV ·
`STUB` = needs fuller bullets before use.

---

## Tag index

Which entries touch which domain (draw from these when a posting emphasises a keyword):

- **Estimation / Kalman / filtering** → Telespazio, Drone GNC, Avionics
- **GNSS / navigation / PNT** → Telespazio, GNSS-R
- **TDOA/FDOA / geolocation / passive radar** → Telespazio
- **Signal processing / RF / SDR** → Telespazio, GNSS-R, CubeSat antenna, Avionics
- **Time & frequency / synchronisation** → Telespazio
- **Embedded / firmware / real-time** → Telespazio, Avionics
- **PCB / hardware / EDA** → Avionics, CubeSat antenna
- **Antennas / electromagnetics** → CubeSat antenna, Avionics
- **Machine learning / data science / HPC** → SAR target recognition
- **Space systems / mission design / orbital** → Mars mission
- **Control / GNC** → Drone GNC, Avionics
- **GPU / HPC** → Telespazio, SAR target recognition

---

## Experiences

### Telespazio — Launcher Trajectory Estimation Intern / Master Thesis  `LIVE`
- **Org:** Telespazio (Leonardo & Thales) — DIANE Station
- **Where / when:** CSG, Kourou, French Guiana · Apr–Oct 2026 (ongoing)
- **Tags:** estimation, Kalman, GNSS/PNT, TDOA/FDOA, geolocation, DSP, RF, SDR,
  time & frequency, embedded, GPU/HPC
- **One-line:** Passive K-station PNT system reconstructing launcher trajectories from
  S-band telemetry reception — full chain from GNSS-disciplined time transfer through coherent
  TDOA/FDOA estimation to trajectory smoothing. Low-cost, fiber-free radar
  alternative; targeting live CSG launch campaign.

**Bullet bank (full — signal/estimation-heavy roles):**
1. **Inverse IQ Measurements to TFDOA position.**: Research, implementation and 
   validation of TFDOA inverse-model. Simulation achieving position accuracy under 20 meters over 500km of range. 
2. **GNSS-disciplined inter-station time transfer** (fiber-free): dual-channel SDR
   samples emitter and GNSS-PPS fiducial on the same ADC clock, cancelling common-mode
   error; matched-filter.
3. **First-principles RF & signal model**: Friis link budget (sky-noise, ENOB, G/T);
   AD9361 direct-conversion model (LO offset, 0.45 ns carrier-phase cycle ambiguity);
   **embedded C firmware** (PlutoPlus/Zynq SoC) for concurrent dual-channel IQ streaming
   at 30.72 MS/s past the non-cacheable-DMA ceiling and onboard PPS edge detection;
   2 ns edge covariance.
4. **Estimation theory & propagation**: CRLB/GDOP station-geometry optimisation
   (Ariane/Vega corridors); equatorial tropospheric delay (Smith-Weintraub + Snell
   ray-trace) at low elevation; FM/AM signal-of-opportunity clock-bias calibration.


**Bullet bank (short — non-specialist / space-generalist roles):**
- Launcher trajectory reconstruction from RF telemetry via a **ground antenna network**;
  **TOA / TDOA / FDOA** geolocation with time synchronisation and multilateration.
- Development and evaluation of **localisation algorithms** for precision, robustness,
  and latency; comparative study of geolocation architectures on an operational
  telemetry system.

> **Reconcile before submitting alongside the thesis docs:** the written project docs
> (06, 09) place the PPS edge detection on the host; bullet 4 says "onboard." Keep one
> story canonical.

---

### GNSS-R Research Intern — ISAE-SUPAERO / DEOS  `LIVE`
- **Org:** ISAE-SUPAERO — DEOS (Electronics, Optronics and Signal Dept.)
- **Where / when:** Toulouse, France · May–Jul 2025
- **Tags:** GNSS, navigation, DSP, RF, remote sensing, bistatic radar
- **One-line:** GNSS reflectometry for terrain characterisation from reflected GPS
  signals; fed active PhD research at DEOS.

**Bullet bank:**
1. Built and applied **Delay-Doppler Map (DDM)** models to extract **terrain
   characteristics** and altitude from reflected GPS signals (**GNSS-R**); analysed
   reflected-signal geometry as a direct application of **bistatic ranging**; results
   fed active PhD research.
2. Worked through **GPS signal architecture from first principles** — signal structure,
   **pseudorange multilateration**, and the bistatic reflectometry observable.
3. (alt) Bibliographic review on **bistatic scattering modelling** and Delay-Doppler
   maps for GNSS-R; feature extraction for Earth observation. *(French-version phrasing;
   use when leaning literature-review rather than build.)*

---

### Avionics Engineer / Team Lead — Fénix Rocket Team & Sunspear Supaero Space  `LIVE`
- **Org:** Fénix Rocket Team (UBI) & Sunspear Supaero Space — EUROC Competition
- **Where / when:** Covilhã, Portugal & Toulouse, France · 2021–2025
- **Tags:** embedded, firmware, real-time, Kalman, GNC, PCB, hardware, RF, telemetry,
  systems, team lead
- **One-line:** Led avionics for a 3 km sounding rocket end-to-end, from STM32 firmware
  to 4-layer PCBs and flight integration.

**Bullet bank:**
1. Led **5-person avionics team** designing avionics for a 3 km rocket: power
   distribution, ejection actuation, telemetry, real-time data handling.
2. Developed **embedded STM32 firmware** (C, STM32CubeIDE) for sensor fusion,
   navigation, fault monitoring, and RF telemetry; implemented **Kalman filters** for
   **attitude estimation** and trajectory control on the target processor.
3. Designed **schematics** and **4-layer PCBs** (antenna, SPI, I²C, USB routing,
   EMC/EMI, power distribution); hands-on prototyping and board bring-up.
4. Performed **component trade-offs** (batteries, converters, sensors, RF modules) under
   mass, power, and thermal budgets and reliability.
5. Validated full avionics stack from **component to system integration**, managing
   hardware redesigns under component-shortage constraints and competition deadlines.

---

## Projects

### SAR Target Recognition by AI — ISAE-SUPAERO & Dassault Systèmes  `HELD`
- **Where / when:** Toulouse · Sep 2025–Mar 2026 · team of 6
- **Tags:** machine learning, data science, HPC, SAR, computer vision
- **Use when:** ML / data-science / defence-imaging postings. Currently out of the base
  CV to keep the navigation focus tight.

**Bullet bank:**
1. Implemented **deep-learning models (CNN, YOLO, R-CNN, Det4SAR)** for **detection and
   classification of military targets** on **SAR data**; optimised training/inference
   pipelines for **HPC**; data pre-processing.

---

### Drone Attitude Estimation & Control — IST  `LIVE`
- **Where / when:** Lisbon · 2024
- **Tags:** control, GNC, Kalman, estimation, sensor fusion, Simulink
- **One-line:** PID + Kalman GNC design for drone attitude, validated in Simulink and on
  a commercial drone.

**Bullet bank:**
1. Designed **PID controllers** and **Kalman estimators** for drone attitude dynamics and
   navigation; built **Simulink** dynamic simulation and validated GNC on a commercial
   drone. Applied **sensor fusion** from IMU data for real-time state estimation.

---

### Mars Satellite Mission — ISAE-SUPAERO  `LIVE`
- **Where / when:** Toulouse · Mar 2025
- **Tags:** space systems, mission design, orbital mechanics, propulsion, systems trade
- **One-line:** Complete Mars mission design — orbital transfer, orbit selection,
  propulsion sizing, and subsystem trade-offs.

**Bullet bank:**
1. Designed complete Mars mission: **orbital transfer optimization**, orbit selection,
   propulsion sizing.
2. Conducted **system-level trade-offs** for avionics, telecom, sensors, power —
   optimising mass, power, performance under constraints (eclipse, structure folding,
   sensor limits); developed **mission architecture** and spacecraft **CAD design**.

---

### CubeSat "Origami" Foldable Antenna — IST  `LIVE`
- **Where / when:** Lisbon · Feb–May 2024
- **Tags:** antennas, electromagnetics, RF, SDR, CST, hardware validation
- **One-line:** Foldable mini-Yagi CubeSat antenna, designed in CST and validated by
  live ADS-B reception.

**Bullet bank:**
1. Designed **foldable mini-Yagi antenna** for CubeSat in **CST Microwave Studio**;
   manufactured and validated with **real-time ADS-B reception** via **RTL-SDR &
   Raspberry Pi**. Met frequency, bandwidth, gain, and polarization requirements.

---

## Education

- **ISAE-SUPAERO** — Aerospace Engineering *Diplôme* · Toulouse · 2024–2026
  - Primary: Data Science and Machine Learning
  - Secondary: Complex Systems and Simulation — Stochastic Mathematics and HPC
  - Electives: Space Propulsion; Planetology, Telescopes, Stellar Physics, Missions
- **Instituto Superior Técnico (IST)** — MSc Aerospace Engineering, Avionics
  specialization · Lisbon · 2023–2026
  - Sensors, Attitude Dynamics, Antennas, Control, Telecommunications,
    Microelectronics, Digital & Analog Signal Processing
- **Universidade da Beira Interior (UBI)** — BSc Aeronautical Engineering · Covilhã ·
  2020–2023
  - Aerodynamics, Structures, Vibrations, Propulsion, Flight Dynamics & Stability,
    CFD, CAD

---

## Skills bank

Pull the subset that matches the posting; don't list everything every time.

- **Languages & tools:** Python, Matlab, C, Java, Bash, Lua, R, Git, Linux, LaTeX
- **Scientific / DSP:** NumPy, SciPy, CuPy (CUDA/GPU), Simulink, GNU Radio, OpenMDAO,
  OpenCV
- **Embedded & SDR:** STM32CubeIDE, ADALM-Pluto / PlutoPlus, real-time Linux
- **EDA & RF:** KiCad, Altium Designer, Cadence, CST Microwave Studio
- **CAD:** SolidWorks, CATIA (surface modelling), XFLR5
- **Lab:** Oscilloscopes, signal generators, spectrum analysers
- **Soft:** systems thinking, adaptability, curiosity

## Languages

Portuguese (native, C2) · English (C1) · Spanish (C1) · French (B2) · Italian (A2)

## Activities

- **Musician:** French horn in orchestras in Portugal and the Toulouse Student Symphony
  Orchestra
- **Teaching:** piano instructor
- **Community:** former Scout
- **Hobbies:** music composition, 3D animation, audio engineering, literature,
  photography
