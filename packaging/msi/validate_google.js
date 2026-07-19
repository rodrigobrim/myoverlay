// MSI UI custom action: validate the Google OAuth client secret the user
// pointed the setup wizard at (GoogleDlg). Sets:
//   GOOGLE_VALID      "1" when the file is a Desktop-app OAuth client secret
//   GOOGLE_VALID_MSG  human-readable result shown in a spawned dialog
function ValidateGoogle() {
    var path = Session.Property("GOOGLE_CLIENT_SECRET");
    var ok = false;
    var msg = "";
    try {
        var fso = new ActiveXObject("Scripting.FileSystemObject");
        if (!path) {
            msg = "Enter the full path to your client_secret JSON file first.";
        } else if (!fso.FileExists(path)) {
            msg = "File not found:\r\n" + path;
        } else {
            var ts = fso.OpenTextFile(path, 1, false);
            var text = ts.ReadAll();
            ts.Close();
            if (text.indexOf('"installed"') >= 0 &&
                text.indexOf('"client_id"') >= 0 &&
                text.indexOf("apps.googleusercontent.com") >= 0) {
                ok = true;
                msg = "Google configuration is valid. Click Next to continue.";
            } else if (text.indexOf('"web"') >= 0) {
                msg = "This is a 'Web application' OAuth client. YouTube upload "
                    + "needs a 'Desktop app' client - create one in Google Cloud "
                    + "Console > Credentials and download its JSON.";
            } else {
                msg = "This file does not look like a Google OAuth client secret "
                    + "(no 'installed' client with a client_id). Download the "
                    + "JSON for your Desktop app OAuth client and try again.";
            }
        }
    } catch (e) {
        msg = "Could not read the file: " + e.message;
    }
    Session.Property("GOOGLE_VALID") = ok ? "1" : "0";
    Session.Property("GOOGLE_VALID_MSG") = msg;
    return 1; // msiDoActionStatusSuccess
}
