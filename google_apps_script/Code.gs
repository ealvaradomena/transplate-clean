/**
 * Transplate human-review helper for the "Translation Review" worksheet.
 * Install this as a bound Apps Script in the restricted Google Sheet.
 * It never clears existing human text when status returns to Pending.
 */
function onEdit(e) {
  if (!e || !e.range) return;
  const sheet = e.range.getSheet();
  if (sheet.getName() !== 'Translation Review' || e.range.getRow() === 1) return;

  const lastColumn = sheet.getLastColumn();
  const headers = sheet.getRange(1, 1, 1, lastColumn).getValues()[0];
  const statusCol = headers.indexOf('status') + 1;
  const draftCol = headers.indexOf('spanish_draft') + 1;
  const humanCol = headers.indexOf('sp_human') + 1;
  if (!statusCol || !draftCol || !humanCol || e.range.getColumn() !== statusCol) return;

  const status = String(e.value || '');
  if (status === 'Keep sp') {
    const draft = sheet.getRange(e.range.getRow(), draftCol).getValue();
    sheet.getRange(e.range.getRow(), humanCol).setValue(draft);
  }
  // Edit sp: human edits sp_human directly.
  // Pending: deliberately do nothing; never erase existing human work.
}
