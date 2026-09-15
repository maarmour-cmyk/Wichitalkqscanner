# Wichita Yard Profit Scanner V3 — iPhone-first

This build is dedicated to:

**Pick Your Part - Wichita**  
700 E 21st St N  
Wichita, KS 67214  
800-962-2277

There is no yard selector.

## iPhone workflow

1. Deploy the app to Streamlit Community Cloud or another Python host.
2. Open the resulting HTTPS URL in Safari.
3. Tap Share → Add to Home Screen.
4. Open it from the Home Screen at the Wichita yard.
5. Paste an LKQ public vehicle page URL or quick-add the car.
6. The TODAY tab ranks the best parts by profit, profit/hour, and score.
7. Enter OEM numbers as you inspect parts.
8. Upload your sold-history/interchange databases to improve accuracy.

## Local run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

Put these files in a GitHub repository, create a new Streamlit app, and point it at `app.py`.

No secrets are required for the basic app.

## Sold-history CSV

Columns:

`year,make,model,part,oem_part_number,sold_price,sold_date`

## Interchange CSV

Columns:

`oem_part_number,part,year_from,year_to,make,model,notes`

## Wichita inventory

The app supports importing a public LKQ/Pick Your Part vehicle-detail URL and extracts, when exposed:
- year
- make
- model
- VIN
- section
- row
- space
- stock number
- available date

Public site structures can change; manual quick-add remains available.

## Important

This scanner does not log into LKQ, bypass CAPTCHAs, evade access controls, or guarantee live inventory.
Always verify the car is still present and the part is still on it before relying on the estimate.
