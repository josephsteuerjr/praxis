# Text-file encoding for the recipient

Some mobile preview versions have misidentified UTF-8 without a BOM,
showing garbled letters while another editor reads the file correctly.
This is a known compatibility class, not a claim about every current
Telegram or phone version.

For an affected recipient, prepare a separate delivery copy with a UTF-8
BOM, using `utf-8-sig` when appropriate. Check initial bytes `EF BB BF`,
contents and the target preview. Do not add a second BOM or change source,
checksums or program files merely for a preview. Before resending, check
the delivery receipt rather than blindly duplicate the message.
