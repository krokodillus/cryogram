---
name: excel-onedrive
description: WHEN a workflow step needs to add rows to or read data from an Excel file stored in OneDrive/SharePoint - the Power Automate bridge for writes, the named-table requirement, date handling, and share-link reads
tier: 2
kind: action
depends_on: []
version: 1.2.0
last_updated: 2026-09-06
---

# Excel in OneDrive

The same split as OneDrive files: writes go through a Power Automate bridge flow (a free trigger, standard connectors, no admin) and reads come through a share link plus openpyxl or pandas. Ask one question up front: add rows, read data, or both?

## The table rule, the most common failure

Every Excel action ("Add a row into a table", "List rows present in a table") works only on a real named Excel table; raw cells in a sheet do not count. User setup: open the file, select the data range (or the headers), Insert, Table with "My table has headers" ticked, then Table Design and give it a name. No table means the flow's dropdown shows nothing, and that symptom means create the table, not rebuild the flow.

## Writes: the locked-in route is the bridge flow

1. https://make.powerautomate.com, Create, Instant cloud flow, the trigger "When a Teams webhook request is received" (Who can trigger: Anyone).
2. The action "Add a row into a table" (Excel Online (Business) for work accounts; for personal accounts the OneDrive file through Excel Online): pick Location, Library, File and Table in the dropdowns, never typing paths, and map each column to the trigger body (triggerBody()?['field']).
3. Save; copy the trigger URL and collect it through the masked ask. The URL is the credential.

The send pattern: POST JSON with one flat object per row; timeout=30; retry timeouts, 5xx and 429 only; success is a 202 with an empty body. It is fire-and-forget: rows cannot be read back through the flow.

Dates: send ISO 8601 text (2026-07-21, or with a time). If a flow reads rows ("List rows present in a table"), set the action's advanced option DateTime Format to ISO 8601, or dates arrive as serial numbers (45465); a time-only column still arrives 1899-based, which is Excel's storage, not a bug. Send numbers as numbers, not quoted strings, or Excel stores text.

## Rows by key, columns by header

Address columns by their header name (the table's column names on the flow side; the header row read once per run on the openpyxl side), never a letter or index, and identify rows by a key value in the row (an id, an email), never a row number, because people sort and insert between runs. A write-back locates the row by key at write time. The code-practice skill has the full rule, identity by content.

## Reads: a share link plus openpyxl

Share the workbook "Anyone with the link", download the bytes the way the onedrive guide's read route does (`?download=1` or `&download=1`, the redirect hosts in `domains`, and the link proven in a cell first), then parse with openpyxl or pandas.read_excel. This reads the saved file, so co-authored edits can lag a few seconds behind what the user sees in the browser.

## What goes wrong

- The flow binds the file by id: renaming or moving the workbook breaks the action silently, so re-pick the file in the flow after any move. Check the flow's run history first when rows stop landing.
- One row per request at one or two a second is fine; for bulk sends pace the requests, with the 429 backoff kept, because Excel Online throttles per file.
- Column mapping is by table header name: renaming a column breaks the mapping the same silent way, so re-open the action after header changes.
- The workbook should be more or less closed: a desktop Excel holding a lock can delay writes, while OneDrive web edits co-author fine.

## If company IT blocks it

The same asks as the onedrive guide: an instant flow with the Teams webhook trigger, or one "Anyone with the link" share for reads. Google Sheets (its own guide) is the alternative when the user is free to choose where the spreadsheet lives.

## Official docs

Check here if a route fails: https://learn.microsoft.com/en-us/connectors/excelonlinebusiness/
