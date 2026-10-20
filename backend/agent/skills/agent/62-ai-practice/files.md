# An AI step that reads a file: send it in a form the model can read

The `[models]` line at the start of each turn says what each model the user set up can read directly: PDFs and images, images only, or text only. That line decides how a step that reads a file is built.

`read_sample` returns a preview of a sample and its stored file reference (`ref`). Pass that reference on the AI step's file input. A file name the user uploaded also finds its reference when only one sample has that name. A label, the file's contents and a path on disk are not the same thing and cannot replace the reference. For nested record outputs, declare `item_fields` before the first call, as the AI guide (62-ai-practice) says.

## When a model reads the file's type

Send the file straight to a model that can read its type, so the model sees the layout and the pictures. Send the whole file when the task needs all of it. Send only a part of it when you have checked that part is enough, so the model does not get content it does not need. Extract text when the step needs exact fields, text it can search, or to send less data. Use OCR only when the text the step needs cannot be got any other way. Choose from what the model can really read and what the sample really holds, not from a general order of methods.

## When no model the user set up reads the file's type

The file never goes to the AI step. A code step before it turns the file into something the AI step's model can read. Which conversion it makes depends on what the workflow needs from the file: decide, and say why in one line.

- **The pages as images**, for an AI step whose model reads images but not PDFs, when the layout carries meaning (a form, a table, a chart, a slide, handwriting, a scan) or when the judgement is about how the page looks. The code step turns each page into an image and the AI step takes the images. Every page costs tokens, so a long document costs more.
- **Text extraction**, when the PDF holds its text as text (not only as a picture of it) and the workflow needs exact values (names, ids, amounts, dates), or when the AI step's model reads only text. It gives the same result every time, costs little, and keeps the exact characters, which a picture of the page does not. It gets nothing from a scan, and it loses the layout and every picture.
- **OCR**, when the PDF is a scan and the model reads only text, or the workflow needs exact values from a scan. It is slower and never exact: a wrong character in an id is the usual mistake, so add a check on the values that matter after it.

A spreadsheet, a Word file or a PowerPoint file is always read by a code step first: no model the user set up takes one whole. The pictures in a Word or PowerPoint file are lost when only the text is extracted; say so when they matter.

## What never happens

Never send a file to a model that cannot read it, and never guess the file's real type from the prompt. Never extract text or run OCR without thinking when that loses layout or pictures the task needs: compare what the task needs with the sample you have.
