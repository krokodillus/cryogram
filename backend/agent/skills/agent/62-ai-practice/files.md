# Files an AI step reads: the file goes whole to a model that reads it, and a code step reads it for one that does not

The models line names what each set-up model reads directly: PDFs and images, images only, or text only. That line decides the shape of a step that reads a file.

## When a model reads the file's type

The file goes to the AI step whole, as its own input, and nothing reads it first: no text extraction, no OCR. A model that reads PDFs reads a difficult PDF (a scan, a two-column layout, a table across pages) better than any extraction, and an extraction in front of it only loses what the model would have read. A file whose content is text goes as text whatever its extension. The kinds a file port takes are filled from the sample the step is tried on, and the step's panel offers only models that read them.

## When no set-up model reads the file's type

The file never goes to the AI step. A code step before it turns the file into what the AI step can take, and which conversion depends on what the workflow needs from the file, so decide it and say why in one line:

- **The pages as images**, for an AI step on a model that reads images but not PDFs, when the layout carries meaning (a form, a table, a chart, a slide, handwriting, a scan) or when the judgement is about how the page looks. The code step renders each page to an image and the AI step takes the images. It costs tokens per page, so a long document pays for every page.
- **Text extraction**, when the PDF has a text layer and the workflow needs exact strings (names, ids, amounts, dates) or the AI step's model reads text only. Deterministic and cheap, and it keeps the exact characters, which a picture of the page does not. It gives nothing on a scan, and it loses the layout and every image.
- **OCR**, when the PDF is a scan and the model reads text only, or the workflow needs exact strings from a scan. Slower and never exact; a wrong character in an id is the usual failure, so a check on the values that matter follows it.

A spreadsheet, a Word or a PowerPoint file is always a code step's to read first: no set-up model takes one whole. The pictures in a Word or PowerPoint file do not survive text extraction; say so when they matter.

## What never happens

Sending a file to a model that cannot read it, which the run refuses before the call. Extracting or OCR-ing a file for a model that could have read it whole. Guessing what a file is from the prompt: the port's kind and the file's real type decide.
