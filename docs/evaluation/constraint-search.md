# Category/filter and evidence audit smoke evaluation

Tested on 2026-11-10 with the existing Gemini 2.5 Flash Lite OpenRouter configuration and
live TME v2 APIs. Input rows came from the existing human-reviewed fixtures and the saved LED
result, read without changing human selections, reviews, counts or run history. Test quantities
were set to one LED or two passives. These four cases are a smoke test, not an accuracy benchmark.

| Case | Selection / result | Search + validation cost | Time |
| --- | --- | --- | --- |
| Saved 1206 LED wrongly assigned an 0805 part | Fail; both inch and metric case conflict | $0.0005517 (validation only) | — |
| LED, `LED_1206_3216Metric` | `FYLS-1206UWC50-RM`; correct 1206/3216 case; attention for unproven pin assignment | $0.0020684 | 28.2 s |
| 10kΩ, `R_0805_2012Metric` | `CRCW080510K0FKEA`; exact value and case; validated match | $0.0014792 | 9.4 s |
| 1µF, `C_0805_2012Metric` | `GRM21BR71A105KA01L`; exact value and case; attention for unsupported commercial evidence references | $0.00134037 | 11.9 s |

The three new searches averaged $0.0016293 including validation. The existing $0.008 row
allowance still applies to all calls and retries. OpenRouter rate limits occurred and were
reported in progress; another capacitor run took 48 seconds. Pricing and latency vary.

The LED search first chose an unsuitable category whose facets could not provide 1206.
The second attempt received that failure and chose a category that exposed the required
case. The API request applied `Case - inch=1206`, `Case - mm=3216` and `Mounting=SMD`.
Resistor/capacitor searches applied exact nominal-value filters as well as case filters.
Results used TME's documented ascending `PRICE_FIRST_QUANTITY` sort, then Python loaded actual
stock, prices and parameters and rejected known conflicts before agent selection.

The inexpensive model still sometimes creates requirements from a selected product or cites
unrelated/missing evidence. Prompts are therefore supplemented by application checks and an
evidence audit. Generic/numeric-only BOMs do not acquire unstated ratings, technology or MPN
requirements. Actual additional BOM properties remain requirements. Pinout cannot pass from
lens, viewing angle, mounting or a package name. Missing pin assignment, range semantics and
unsupported evidence stay visible as attention. Automatic datasheet/pinout extraction is outside
this change, so a correct package does not establish full electrical compatibility.

Automated regressions cover chip case codes for LEDs/R/C, inch and metric conflicts, missing
geometry, mounting synonyms, documented geometry alternatives, exact and minimum ratings,
colour, tolerance, nominal values, ambiguous ranges, mandatory filters, invented filter labels,
recovery feedback, pagination and sorting, misleading model passes, and non-applicable checks.

API contract: [TME v2 documentation](https://developers.tme.eu/en/api-doc/v2),
[OpenAPI schema](https://developers.tme.eu/openapi.json).
