# Split mother email into Gmail plus variants

This helper works with the `api_code` mail provider.

Input line format in `config.yaml`:

```yaml
mail:
  providers:
    - type: api_code
      enable: true
      label: API Code Pool
      mailboxes: |
        your-main@gmail.com----https://example.com/api/code/fetch?token=TOKEN&uid=UID
```

Generate 4 variants for every plain mother email:

```powershell
.\分裂母邮箱.bat 4
```

Generate 10 variants for one specified mother email:

```powershell
.\分裂母邮箱.bat 10 your-main@gmail.com
```

Equivalent Python command:

```powershell
.\.venv\Scripts\python.exe .\scripts\split_mother_email.py --count 4 --email your-main@gmail.com
```

Output lines look like:

```text
your-main+abcdef@gmail.com----same-fetch-url
your-main+ghijkl@gmail.com----same-fetch-url
```

Notes:

- Existing addresses are deduplicated.
- A timestamped backup of `config.yaml` is created before writing.
- By default the mother email line is kept. Add `--remove-mother` to remove it.
- If `config.yaml` already contains only plus variants and no mother line, run with `--email your-main@gmail.com` to derive new variants from that mother address.