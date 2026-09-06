# Nonda Inventory System — final update

This bundle is reconciled against the supplied `combined_project(4).txt`.

## Replace
Copy the files under `web/` into the corresponding project locations, preserving the `services/` and `templates/pipeline/` paths.

## What is fixed
1. KG inventory uses the standalone `StockStage` enum everywhere.
2. The movement ledger now queries `StockMovement`, not `CoffeeStock`.
3. The dashboard no longer references the removed `quantity_packaged` field.
4. Processing view uses the existing processing service API for roasting/grinding.
5. Packaging validates the product form against the source stage and locks the stock row during the transaction.
6. Packaged-product creation exposes `selling_price`.
7. Direct pack-release form uses an actual active sales-user `ModelChoiceField` instead of a text input for a ForeignKey.
8. Request fulfilment locks packaged inventory and preserves release-price history.
9. Returns ensure the packaged inventory row exists and update release status correctly.
10. The missing `packaging_run_form.html` is supplied.
11. Stock-request form remains a normal Django formset and its client-side row management is kept deterministic.
12. Regression tests cover receipt → roast → grind → package → request → release → return.

## Database
No new migration is required by these changes. The supplied model already has `PackRelease.selling_price`, and migration `0003` already creates it.

Run:

    python manage.py check
    python manage.py makemigrations
    python manage.py migrate
    python manage.py test web

`makemigrations` should produce no changes if the supplied models are the models currently installed.
