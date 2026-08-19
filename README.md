
# Nonda Commodities

# Automated Sample-to-Contract & Customer Supply Management System

## 1. Executive Summary

### Background

Nonda Commodities relies heavily on coffee sampling to acquire new customers, particularly international buyers, cafes, hotels, restaurants, distributors, and wholesalers.

Currently, sample distribution, customer follow-up, and conversion tracking are largely manual processes. This creates operational inefficiencies, inconsistent customer engagement, and missed sales opportunities.

Prospective buyers may receive samples but fail to receive timely follow-up communication, causing potentially valuable leads to be lost before a purchasing agreement is established.

### Business Challenge

The current process lacks:

* Structured lead management
* Consistent follow-up procedures
* Sample tracking visibility
* Customer journey visibility
* Conversion measurement
* Integrated inventory accountability

As a result, management has limited visibility into:

* How many samples are sent
* Which prospects are actively engaged
* Which samples convert into contracts
* Why certain prospects are lost
* How inventory is consumed through samples and deliveries

### Business Objective

The objective of the system is to create a centralized platform that tracks prospects from their first interaction through long-term recurring supply relationships.

### Core Business Hypothesis

> If every sample recipient receives consistent, timely, and personalized follow-up communication, the sample-to-contract conversion rate will increase significantly.

---

# 2. Strategic Business Goals

The system is designed to achieve the following business outcomes:

### Revenue Growth

Increase the number of contracts generated from coffee samples.

### Lead Retention

Eliminate lead leakage caused by missed follow-ups.

### Customer Relationship Management

Provide visibility into every customer's stage in the sales journey.

### Inventory Accountability

Track inventory consumption for both samples and customer deliveries.

### Operational Efficiency

Reduce manual tracking and administrative workload.

### Data-Driven Decisions

Provide management with conversion metrics and sales intelligence.

---

# 3. Customer Lifecycle Journey

Every company enters the system as a prospect.

```text
New Lead
    │
    ▼
Sample Sent
    │
    ▼
Sample Received
    │
    ▼
Follow-Up
    │
    ▼
Negotiation
    │
    ▼
Contract Signed
    │
    ▼
Recurring Supply
```

Alternative Outcome:

```text
New Lead
    │
    ▼
Sample Sent
    │
    ▼
Follow-Up
    │
    ▼
Lost
```

---

# 4. Phase 1 MVP Scope

The MVP focuses on validating the core business hypothesis before investing in advanced infrastructure.

### Goal

Prove that structured follow-up increases sample-to-contract conversion rates.

### Included Features

#### Company Management

* Register prospects
* Store contact information
* Track customer pipeline stage

#### Coffee Inventory Management

* Track available roasted coffee stock
* Monitor reorder levels
* Record coffee origin and washing station

#### Sample Management

* Record samples sent
* Track courier information
* Track delivery status
* Automatically deduct sample inventory

#### Follow-Up Tracking

* Track follow-up milestones
* Record communication history
* Track conversion status

#### Sample Feedback Collection

* Customer ratings
* Customer comments
* Purchase interest indicators

#### Contract Management

* Record signed contracts
* Store pricing agreements
* Store delivery schedules

#### Supply Fulfillment Tracking

* Track recurring deliveries
* Record shipment volumes
* Track invoice numbers

---

# 5. System Modules

## Module 1: Company Management

Purpose:

Store and manage all prospect and customer records.

Key Information:

* Company Name
* Contact Person
* Email
* Phone Number
* Address
* Pipeline Stage

---

## Module 2: Coffee Inventory

Purpose:

Track coffee stock available for sampling and fulfillment.

Key Information:

* Coffee Type
* Variety Name
* Washing Station
* Roast Date
* Quantity Available
* Reorder Threshold

---

## Module 3: Sample Tracking

Purpose:

Track all samples distributed to prospects.

Key Information:

* Recipient Company
* Coffee Sample
* Sample Weight
* Courier
* Tracking Number
* Delivery Status

Business Rule:

Whenever a sample is sent:

```text
Inventory decreases automatically.
```

---

## Module 4: Follow-Up Management

Purpose:

Ensure no prospect is forgotten.

Current Workflow:

```text
Dispatch Alert
      ↓
Day 3 Follow-Up
      ↓
Day 7 Follow-Up
      ↓
Contract Proposal
```

Management Benefit:

Every sample receives structured engagement.

---

## Module 5: Feedback Collection

Purpose:

Capture customer reactions to coffee samples.

Information Collected:

* Rating
* Comments
* Contract Interest

Management Benefit:

Understand which coffees generate the highest interest.

---

## Module 6: Contract Management

Purpose:

Convert prospects into active customers.

Information Stored:

* Contract Volume
* Price Per Kilogram
* Delivery Frequency
* Contract Status

---

## Module 7: Supply Fulfillment

Purpose:

Manage recurring customer deliveries.

Information Stored:

* Delivery Volume
* Delivery Date
* Invoice Number

Business Benefit:

Tracks customer fulfillment history.

---

# 6. Management Dashboard KPIs

The system will eventually provide:

### Sales Metrics

* Total Leads
* Samples Sent
* Samples Delivered
* Follow-Ups Completed
* Contracts Signed
* Conversion Rate

### Inventory Metrics

* Available Stock
* Samples Consumed
* Customer Deliveries
* Low Stock Alerts

### Customer Metrics

* Active Customers
* Prospects
* Lost Opportunities

---

# 7. Future Enhancements (Phase 2)

This is where WhatsApp belongs.

### Automated Email Campaigns

* Dispatch notifications
* Day 3 educational content
* Day 7 contract proposals

### WhatsApp Integration

Potential providers:

* Africa's Talking
* Meta WhatsApp Business API
* Twilio

Capabilities:

* Automated sample delivery notifications
* Follow-up reminders
* Contract discussions
* Delivery notifications
* Customer support communication

### PDF Generation

Automatically generate:

* Cupping guides
* Product catalogues
* Contracts
* Invoices

### Logistics Integration

Future integration with:

* DHL
* FedEx
* UPS

### Advanced Reporting

* Coffee popularity analysis
* Conversion trend analysis
* Customer lifetime value
* Revenue forecasting

---

# 8. Success Criteria

The MVP will be considered successful if it demonstrates:

* Increased follow-up consistency
* Reduced lead leakage
* Improved sample-to-contract conversion rate
* Better inventory visibility
* Better customer tracking

### Primary KPI

> Sample-to-Contract Conversion Rate

If this metric improves, the business case for WhatsApp automation, advanced analytics, logistics integrations, and larger-scale CRM functionality becomes clear.

---

# 9. Stock Intake Behaviour

Coffee is received on one form (`Add Stock`) that asks for a **name of material**
instead of a variety dropdown:

* The field is backed by a datalist of every active `CoffeeVariety`, each option
  carrying that variety's master defaults (coffee type, grade, source, process,
  foreign smell). Selecting or typing a known name prefills the empty batch
  fields client-side.
* On submit, the name is resolved case-insensitively:
  * **Known name** — the existing `CoffeeVariety` is reused. If the supplier and
    receiving date match a batch already on file, the intake tops up that batch;
    otherwise a new, separately traceable batch is opened under the same variety.
  * **New name** — a new `CoffeeVariety` definition is auto-generated from the
    batch details, plus its first batch.
* Quantities are never written onto the batch row. Every intake posts a
  `receipt` `StockMovement`, so the movement ledger stays the single source of
  truth for what is available at each processing stage (green, roasted, ground,
  packaged) and samples deduct from it.

# 10. Running Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# .env
# SECRET_KEY=...
# DEBUG=True
# ALLOWED_HOSTS=127.0.0.1,localhost

python manage.py migrate
python manage.py test
python manage.py runserver
```
