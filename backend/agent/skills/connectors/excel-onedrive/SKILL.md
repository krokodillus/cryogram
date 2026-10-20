---
name: excel-onedrive
description: WHEN a step reads or writes an Excel workbook in OneDrive or SharePoint: which routes are possible, and why the data must be in a named Excel table
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Excel in OneDrive

Use the direction the user asked for (read or write). Ask only if it is unclear. A Power Automate write bridge needs a work/school account that owns the flow, permission from the tenant, and the connector it uses. Managing cloud flows with a personal account ended in July 2025, so a personal OneDrive account alone does not meet this. For private reads, prefer access that is already authorised. Use a public share link only if the user explicitly decides to share the file that way.

## The table rule: the most common failure

Every Excel action ("Add a row into a table", "List rows present in a table") works only on a real, named Excel table. Cells in a sheet that are not a table do not count. Give the user these steps: open the file, select the data range (or just the headers), choose Insert, then Table, tick "My table has headers", then open Table Design and give the table a name. If there is no table, the flow's dropdown shows nothing. When you see that, tell the user to create the table. Do not rebuild the flow.

## Writes: a bridge flow, when the requested setup allows it

1. Go to https://make.powerautomate.com and choose Create, then Instant cloud flow, then the trigger "When a Teams webhook request is received" (Who can trigger: Anyone).
2. Add the action "Add a row into a table". Use Excel Online (Business) for work accounts. For personal accounts, use the OneDrive file through Excel Online. Pick Location, Library, File and Table in the dropdowns, and never type paths. Map each column to the trigger body (triggerBody()?['field']).
3. Save the flow, copy the URL of the trigger and ask the user for it with the masked ask. The URL is the only credential, so treat it as a secret.

POST one agreed row with an explicit timeout and one attempt. A 202 means only that the trigger accepted the request, not that the row was added. During testing, check the downstream action in the flow's run history, or read the row back with access that is already authorised. If the outcome is unclear, find out what happened before you retry. This template does not send rows back to the caller.

Dates: send dates as ISO 8601 text (2026-07-21, or with a time). If a flow reads rows ("List rows present in a table"), set the action's advanced option DateTime Format to ISO 8601, or dates arrive as serial numbers (45465). A column that holds only a time still arrives as a date in 1899. That is how Excel stores it, not a bug. Send numbers as numbers, not as quoted strings, or Excel stores them as text.

## Find rows by a key and columns by their header

Address columns by their header name: on the flow side, the table's column names; on the openpyxl side, the header row, read once per run. Never use a column letter or an index. Identify rows by a key value in the row (an id, an email), never by row number, because people sort the rows and insert rows between runs. When the step writes back to a row, it finds the row by its key at the moment of writing. The code-practice skill has the full rule: identify a row by its content.

## Reads: a share link and openpyxl

Use private access that is already authorised if there is any. If the user explicitly agrees to a link that is public to anyone, follow the onedrive guide's download route, check the downloaded bytes and read them with openpyxl or pandas. Read only the file the user asked for. The saved file may not yet include edits that people are making right now in live co-authoring.

## What goes wrong

- The flow finds the file by its id. If the workbook is renamed or moved, the action fails without any message, so pick the file again in the flow after any move. When rows stop arriving, look at the flow's run history first.
- Pace writes at the rate the user approved, within the service's current limits. On a 429, stop and find out what was written before you continue. Do not automatically retry a row creation whose outcome is unclear.
- Columns are mapped by the table's header names. Renaming a column breaks the mapping, again without any message, so open the action again after any header change.
- Keep the workbook closed in desktop Excel where possible. A desktop Excel that holds a lock can delay writes. Edits in OneDrive on the web co-author without trouble.

## If company IT blocks it

Give IT the same requests as in the onedrive guide: an instant flow with the Teams webhook trigger, or one "Anyone with the link" share for reads. If the user is free to choose where the spreadsheet lives, Google Sheets (its own guide) is the alternative.

## Official docs

If a route fails, check here: https://learn.microsoft.com/en-us/connectors/excelonlinebusiness/
