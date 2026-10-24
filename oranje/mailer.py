"""Open an Outlook draft locally. Never send mail or change invoice status."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile


# All variable data travels over stdin as JSON, never as executable PowerShell.
OUTLOOK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $data = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $outlook = New-Object -ComObject Outlook.Application
    $mail = $outlook.CreateItem(0)
    $mail.BodyFormat = 1
    $mail.To = [string]$data.recipient
    $mail.Subject = [string]$data.subject
    $mail.Body = [string]$data.body
    $mail.Save()
    $attachment = $mail.Attachments.Add([string]$data.attachment, 1)
    $mail.Save()
    $mail.Display($false)
    exit 0
} catch {
    # Do not print message contents, contact details or local paths.
    exit 1
}
"""


def open_outlook(message):
    if sys.platform != 'win32':
        raise ValueError('Auto mail werkt met de klassieke Outlook op Windows.')
    with tempfile.TemporaryDirectory(prefix='oranje-mail-') as directory:
        # The reference is only a display filename; never accept a path from it.
        filename = ''.join(c if c.isascii() and (c.isalnum() or c in '-_') else '-' for c in message['reference'])[:160] or 'afrekening'
        attachment = Path(directory) / (filename + '.pdf')
        attachment.write_bytes(message['pdf'])
        payload = {k: message[k] for k in ('recipient', 'subject', 'body')}
        payload['attachment'] = str(attachment.resolve())
        command = base64.b64encode(OUTLOOK_SCRIPT.encode('utf-16le')).decode('ascii')
        try:
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', command],
                input=json.dumps(payload, ensure_ascii=False), encoding='utf-8',
                capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            raise ValueError('Outlook reageert nog niet. Controleer eerst Outlook en de map Concepten voordat je opnieuw klikt; er kan al een concept klaarstaan.') from None
        except OSError:
            raise ValueError('Outlook kon niet worden geopend. Gebruik de klassieke Outlook op Windows, of kopieer de mail en voeg de pdf zelf toe.') from None
        if result.returncode:
            raise ValueError('Outlook kon het concept niet openen. Open en configureer eerst de klassieke Outlook op deze pc. De nieuwe Outlook ondersteunt deze koppeling niet. Controleer ook Concepten; je kunt de mail en pdf via Openen handmatig overnemen.')
    return {'ok': True, 'message': 'Het concept staat klaar in Outlook, inclusief pdf. Controleer de afzender en ontvanger en verstuur zelf. Klik daarna op Verstuurd registreren.'}
