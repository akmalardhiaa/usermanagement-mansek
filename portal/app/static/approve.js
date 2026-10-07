// The approval email's "Setujui" link opens this page; this posts the approval
// in one click. A link scanner fetching the URL runs no script, so it cannot.
document.addEventListener("DOMContentLoaded", function () {
  var form = document.querySelector("form[data-auto-submit]");
  if (form) form.submit();
});
