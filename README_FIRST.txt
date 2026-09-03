Nonda Phase 1 — Sales / Store / Cashier workflow

This bundle is based on the supplied combined_project.txt and keeps the existing single `web` app.

COPY/REPLACE THESE:
web/models.py
web/forms.py
web/views.py
web/urls.py
web/services/packaging.py
web/templates/pipeline/base.html
web/templates/pipeline/pack_release_detail.html
web/templates/pipeline/pack_release_list.html
web/templates/pipeline/pack_return_form.html
web/templates/pipeline/pack_return_list.html

ADD THESE:
web/sales_views.py
web/sales_forms.py
web/sales_workflow.py
web/templates/pipeline/admin_dashboard.html
web/templates/pipeline/inventory_dashboard.html
web/templates/pipeline/sales_workspace.html
web/templates/pipeline/stock_request_form.html
web/templates/pipeline/stock_request_list.html
web/templates/pipeline/stock_request_detail.html
web/templates/pipeline/stock_request_fulfill.html
web/templates/pipeline/sales_stock.html
web/templates/pipeline/order_queue.html
web/templates/pipeline/cashier_clearance.html

Then from the project root:
python manage.py makemigrations web
python manage.py migrate
python manage.py check
python manage.py test web

IMPORTANT:
Do not delete existing migrations. Let Django create the next migration from models.py.
The existing roasted bulk sale model remains in the database/code but is intentionally removed from the navigation for now.

Role routing:
ADMIN -> admin_dashboard
MANAGER -> inventory_dashboard
SALES -> record_sale
CASHIER / legacy ACCOUNTS -> order_queue

Physical accountability:
Stock Request -> Pack Release -> Pack Return

Money accountability:
Pack Release -> Pack Settlement -> Cashier clearance

Price is captured on PackRelease as a historical snapshot of the price attached when stock left the store.
the combined pr