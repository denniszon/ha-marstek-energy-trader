# Marstek Energy Trader

Home Assistant-integratie die een **Marstek Venus E** automatisch laat laden en ontladen op basis van de stroomprijs, de zonneverwachting en je verbruik. De integratie stuurt de batterij aan via de [Marstek Venus Modbus](https://github.com/ViperRNMC/marstek_venus_modbus)-integratie.

> [!WARNING]
> Dit is geen officieel Marstek-product. De integratie zet de batterij in RS485-controlemodus en schrijft vermogens en modi via Modbus. **Begin met *Proefdraaien* aan** (standaard) en kijk een paar dagen mee voordat je de batterij echt laat aansturen. Gebruik op eigen risico.

## Wat doet het?

- **Plant vooruit**: voor elk uur (of kwartier) waarvoor een prijs bekend is, kiest de integratie een actie:
  - 🔋 **Laden** uit het net (goedkoop of negatieve prijs)
  - ⚡ **Ontladen** (duur; naar het huis, of ook naar het net als je dat toestaat)
  - ⏸️ **Vasthouden**: energie bewaren voor een duurder moment
  - 🏠 **Zelfverbruik**: de Marstek volgt zelf je verbruik en slaat zonne-overschot op
- **Rekent alles door**: rendementsverlies, minimale winst per kWh, reserve, laad/ontlaadvermogen, verwachte zon en verbruik. De planner zoekt de goedkoopste combinatie over de hele horizon (dynamisch programmeren), dus "goedkope nacht → dure avond" ook als er tussendoor nog goedkope uren zijn.
- **Legt uit waarom**: elke actie heeft een reden, bijvoorbeeld *"Laden voor €0.14: de energie wordt om 18:00 gebruikt bij €0.42"*. Die staat in de status-sensor, de grafiek, het logboek en de planning.
- **Houdt de besparing bij**: gemeten met de energietellers van de Marstek en je P1-meter, in euro's per dag/maand en in kWh-stromen.

## Modi

| Modus | Wat het doet |
|---|---|
| **Uit** | Doet niets; zet de Marstek één keer terug op zelfverbruik. |
| **Alleen zon** | Altijd zelfverbruik: zonne-overschot opslaan en 's avonds gebruiken. Nooit laden uit het net. |
| **Alleen prijs** | Laden, ontladen en vasthouden op prijs. Geen zelfverbruik-modus. |
| **Zon en prijs** | Zelfverbruik als basis, plus laden uit het net als dat loont en er te weinig zon komt, en energie vasthouden voor dure uren. |

**Proefdraaien** (schakelaar, staat standaard aan) werkt in elke modus: er wordt gepland en uitgelegd, en je ziet welke schrijfacties het *zou* doen, maar er gaat niets naar de Marstek. De sensor *Geplande besparing totaal* telt dan op wat het zou hebben opgeleverd.

## Vereisten

- Home Assistant **2026.9** of nieuwer.
- [Marstek Venus Modbus](https://github.com/ViperRNMC/marstek_venus_modbus) met je Venus E. Zet in die integratie deze entiteiten **aan** (ze staan standaard uit): *Force Mode*, *Set Charge Power*, *Set Discharge Power* en *RS485 Control Mode*. Ook nodig: *User Work Mode* en *Battery SOC*.
  Voor de besparing: *Total Charging Energy*, *Total Discharging Energy* en *Battery Power*.
- Voor zelfverbruik moet de Marstek zelf een meter hebben (CT of P1 in de Marstek-app), want dan regelt de Marstek de nul-op-de-meter.
- Een **prijssensor met de prijzen van de komende uren als attribuut**. Getest formaat: `raw_today`/`raw_tomorrow` (bijvoorbeeld [ANWB Energie](https://github.com/denniszon/ha-anwb-energie) of Nord Pool). Ook herkend: `prices_today`/`prices_tomorrow` (ENTSO-e) en `prices`.
- Optioneel:
  - **Netvermogen** (bijvoorbeeld HomeWizard P1, in W): om net- en zonne-energie uit elkaar te houden.
  - **Zonneverwachting**: Forecast.Solar (standaard in Home Assistant) of Solcast (HACS).
  - **Weer**: grove zonschatting uit bewolking als er geen zonneverwachting is.
  - **Verbruikssensor**: het gemiddelde per uur van de afgelopen 7 dagen wordt gebruikt.
- Voor de grafieken: [apexcharts-card](https://github.com/RomRider/apexcharts-card) (HACS → Frontend).

## Installatie via HACS

1. HACS → ⋮ → **Custom repositories** → URL `https://github.com/denniszon/ha-marstek-energy-trader`, type **Integration**.
2. Zoek **Marstek Energy Trader**, installeer en herstart Home Assistant.
3. Instellingen → Apparaten en diensten → **Integratie toevoegen** → *Marstek Energy Trader*.
4. Kies je Marstek, de prijssensor, de terugleverprijs, en optioneel de meter, zon en verbruik. Alles is later aan te passen via **Configureren**.

Updates verschijnen in HACS zodra er een nieuwe release is.

### Instellingen bij het toevoegen

| Stap | Instelling | Uitleg |
|---|---|---|
| Batterij | Teken batterijvermogen | Kijk tijdens laden naar *Battery Power* van de Marstek: positief of negatief? |
| | Capaciteit | Leeg = wat de batterij doorgeeft. |
| | Rendement | Laden + ontladen samen, standaard 85%. |
| Prijzen | Prijssensor | Wat je per kWh betaalt, incl. belastingen (bijv. `sensor.anwb_energie_stroom_all_in`). |
| | Terugleverprijs | Gelijk aan de prijs (saldering), aparte sensor, prijs ± bedrag of vast bedrag. |
| | Terugleverkosten | Gaat van elke terugleverprijs af. |
| Meter, zon, verbruik | Netvermogen + teken | HomeWizard P1: positief bij afname. |
| | Zonneverwachting | Forecast.Solar (kies de configuratie) of Solcast (sensoren *Forecast Today/Tomorrow*). |
| | Weer + piekvermogen | Alleen voor de schatting op basis van bewolking. |
| | Verbruik | Sensor in W of kWh; anders het vaste gemiddelde verbruik. |

## Bediening (dashboard)

| Entiteit | Wat het doet |
|---|---|
| `select.marstek_energy_trader_mode` | Modus (zie boven). |
| `switch.marstek_energy_trader_dry_run` | Proefdraaien aan/uit. |
| `select.marstek_energy_trader_strategy` | **Voorzichtig / Gebalanceerd / Agressief**: zet de minimale winst en de reserve. Pas je een getal aan, dan wordt het *Aangepast*. |
| `number.marstek_energy_trader_min_profit` | Minimale winst per ontladen kWh (na verliezen). Hoger = minder vaak handelen, minder slijtage. |
| `number.marstek_energy_trader_min_soc` / `max_soc` | Reserve en maximum batterijniveau. |
| `number.marstek_energy_trader_max_charge_power` / `max_discharge_power` | Maximaal vermogen dat de planner gebruikt (Marstek: tot 2500 W). |
| `number.marstek_energy_trader_forecast_trust` | Hoeveel procent van de zonneverwachting de planner gelooft. |
| `switch.marstek_energy_trader_allow_grid_export` | Mag de batterij meer leveren dan het huis gebruikt (terugleveren)? Uit: ontladen gaat via zelfverbruik, zodat de Marstek je echte verbruik volgt. |
| `select.marstek_energy_trader_override` + `number..._override_hours` | **Handmatig ingrijpen**: laden/ontladen/vasthouden/zelfverbruik voor de komende uren. Daarna weer automatisch. |
| `button.marstek_energy_trader_replan` | Direct opnieuw plannen. |

Strategieën:

| Strategie | Minimale winst | Reserve |
|---|---|---|
| Voorzichtig | €0,08/kWh | 30% |
| Gebalanceerd | €0,04/kWh | 20% |
| Agressief | €0,02/kWh | 11% |

### Diensten

- `marstek_energy_trader.set_override`: `action`, optioneel `start`, `end` of `hours`, en `power_w`. Een handmatige ontlading gebruikt het gekozen vermogen, ook als dat terugleveren betekent. Handig in automatiseringen, bijvoorbeeld "laad morgen 02:00–05:00 de auto en houd de batterij vast".
- `marstek_energy_trader.clear_override`: alle handmatige acties wissen.
- `marstek_energy_trader.replan`: opnieuw plannen.

## Sensoren

| Entiteit | Inhoud |
|---|---|
| `sensor.marstek_energy_trader_status` | Huidige actie. Attributen: `reason`, `power_w`, `marstek_action` (wat de Marstek echt doet), `writes`, `decisions` (laatste 20 beslissingen), `last_error`. |
| `sensor.marstek_energy_trader_plan` | Verwachte besparing van de planning (€). Attribuut `plan`: per tijdvak `start`, `end`, `action`, `power_w` (+ laden, − ontladen), `soc_start`/`soc_end`, `buy`/`sell`, `solar_kwh`, `load_kwh`, `grid_kwh`, `savings`, `reason`. |
| `sensor.marstek_energy_trader_next_change` | Wanneer de actie verandert, en naar wat. |
| `sensor.marstek_energy_trader_target_power` / `planned_soc` | Gepland vermogen en batterijniveau nu (voor "werkelijk vs. gepland"). |
| `sensor.marstek_energy_trader_savings_total` / `savings_today` | Gemeten besparing in €. |
| `sensor.marstek_energy_trader_planned_savings_total` | Wat de planning verwachtte (ook tijdens proefdraaien). |
| `sensor.marstek_energy_trader_charge_cost_total` / `discharge_value_total` | Kosten van laden en waarde van ontladen. |
| `sensor.marstek_energy_trader_grid_to_battery` / `solar_to_battery` / `battery_to_home` / `battery_to_grid` | Energiestromen in kWh. |
| `sensor.marstek_energy_trader_average_stored_price` | Gemiddelde prijs van de energie die nu in de batterij zit. |

Elke wijziging van actie komt met de reden in het **logboek**, bij proefdraaien met `[dry run]` ervoor.

## Grafieken

In [`examples/`](examples/) (apexcharts-card nodig):

- [`dashboard.yaml`](examples/dashboard.yaml): een compleet dashboard met alle kaarten hieronder.
- [`status-card.yaml`](examples/status-card.yaml): wat hij nu doet, waarom, en een tabel van de komende 12 tijdvakken.
- [`plan-chart.yaml`](examples/plan-chart.yaml): prijs per uur, gekleurd per geplande actie, met het geplande batterijniveau. Hover voor de reden.
- [`energy-chart.yaml`](examples/energy-chart.yaml): gepland batterijvermogen naast verwachte zon en verbruik.
- [`controls-card.yaml`](examples/controls-card.yaml): alle instellingen en handmatig ingrijpen.
- [`savings-cards.yaml`](examples/savings-cards.yaml): besparing vandaag/totaal, per dag en per maand, en energiestromen.
- [`reality-chart.yaml`](examples/reality-chart.yaml): werkelijk batterijniveau en vermogen naast de planning.

## Hoe werkt het?

**Planning.** Elk kwartier, bij nieuwe prijzen en bij elke wijziging van een instelling maakt de integratie een nieuwe planning vanaf nu tot de laatste bekende prijs. Per tijdvak rekent hij met:

- netto verbruik = verwacht verbruik − verwachte zon × vertrouwen;
- kosten = afname × prijs − teruglevering × terugleverprijs;
- elke ontladen kWh moet minstens de *minimale winst* opleveren;
- verlies: de helft van het rendementsverlies bij laden, de andere helft bij ontladen;
- energie die aan het eind van de planning nog in de batterij zit, telt mee voor de mediane prijs (min verlies en minimale winst), zodat hij de batterij niet zomaar leegtrekt vlak voordat de prijzen van morgen bekend zijn.

**Aansturing.** Alleen wat afwijkt wordt geschreven:

| Actie | Naar de Marstek |
|---|---|
| Laden | RS485 aan → laadvermogen → force mode *charge* |
| Ontladen (terugleveren toegestaan) | RS485 aan → ontlaadvermogen → force mode *discharge* |
| Ontladen (niet terugleveren) | user work mode *zelfverbruik* (volgt je echte verbruik) |
| Vasthouden | RS485 aan → force mode *standby* |
| Zelfverbruik / Uit / geen gegevens | user work mode *zelfverbruik*, RS485 uit |

De Marstek kent de reserve van de planner niet. Staat hij in zelfverbruik op of onder de reserve terwijl het huis stroom van het net haalt, dan zet de integratie hem op *standby*, zodat de reserve blijft staan.

Bij **proefdraaien aanzetten**, de modus **Uit** en het **verwijderen of herladen** van de integratie gaat de Marstek terug naar zelfverbruik, zodat hij nooit in een geforceerde modus blijft hangen.

Een **watchdog** (standaard elke 5 minuten) controleert of de Marstek nog doet wat hij moet doen en schrijft opnieuw als dat niet zo is. Gebruikers van de Modbus-integratie melden dat de RS485-modus soms vanzelf terugvalt. Als er geen prijzen zijn, valt de integratie terug op zelfverbruik.

**Besparing.** Elke 30 seconden kijkt de integratie hoeveel de energietellers van de Marstek zijn opgelopen, en met de batterij- en netvermogens waar die energie vandaan kwam of naartoe ging:

- laden uit het net kost de prijs, laden uit zon kost de terugleverprijs die je misloopt;
- ontladen naar het huis bespaart de prijs, ontladen naar het net levert de terugleverprijs op.

Zonder netvermogen-sensor telt laden als "uit het net" en ontladen als "naar het huis".

## Bekende beperkingen

- Alleen Marstek Venus via de Marstek Venus Modbus-integratie.
- Het gedrag van RS485/force mode verschilt per firmware (gemeld voor V148/V150). Test eerst met proefdraaien.
- De verbruiksverwachting is eenvoudig (gemiddelde per uur van de dag). Grote verbruikers (auto, warmtepomp) kun je met een handmatige actie afvangen.
- De weer-schatting is grof; gebruik bij voorkeur Forecast.Solar of Solcast.

## Ontwikkeling

```bash
python -m venv .venv && . .venv/bin/activate
pip install pytest-homeassistant-custom-component ruff
ruff check . && ruff format --check .
pytest -q tests
```

GitHub Actions draait de HACS- en hassfest-validatie en de tests.
