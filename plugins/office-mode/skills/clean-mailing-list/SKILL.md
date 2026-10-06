---
name: clean-mailing-list
description: Tidy up a mailing list or contact list (Excel or CSV) before sending a newsletter or importing it somewhere. Removes duplicate email addresses (even if the capitals differ), trims stray spaces, sets aside addresses that look wrong, and splits full names into first and last names. Saves a new cleaned copy and leaves the original alone.
---

# Clean a mailing list

Use this when someone wants a contact or mailing list tidied, de-duplicated or made ready to import
into a newsletter tool such as Mailchimp or MailerLite.

## Steps

1. Look at the first few rows to see which column holds email addresses and which holds names.
2. Run the bundled script. It writes a new file and never changes the original:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/clean-mailing-list/scripts/clean_list.py" "<list file>" \
     [--email-col "Email"] [--name-col "Name"] [--sheet "<sheet>"] [--out "<name> - cleaned.csv"]
   ```

   It finds the email and name columns by itself when they are called something obvious.
3. Tell the person, in plain words: how many contacts they started with, how many are in the
   cleaned list, how many duplicates were removed, and how many addresses need a look. Name both
   files it saved ("- cleaned.csv" and, if there were problems, "- problems.csv") and their folder.
4. If they want the problem rows fixed, go through the problems file with them. Suggest obvious
   typo fixes (e.g. "gmial.com" → "gmail.com") but ask before changing anyone's address.
