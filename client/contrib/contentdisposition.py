# stolen from http://stackoverflow.com/questions/8035900/how-to-get-filename-from-content-disposition-in-headers
import re
import urllib
from os import path

# content-disposition = "Content-Disposition" ":"
#                        disposition-type *( ";" disposition-parm )
# disposition-type    = "inline" | "attachment" | disp-ext-type
#                     ; case-insensitive
# disp-ext-type       = token
# disposition-parm    = filename-parm | disp-ext-parm
# filename-parm       = "filename" "=" value
#                     | "filename*" "=" ext-value
# disp-ext-parm       = token "=" value
#                     | ext-token "=" ext-value
# ext-token           = <the characters in token, followed by "*">

def parse(content_disposition):
    token = '[-!#-\'*+.\dA-Z^-z|~]+'
    qdtext='[]-~\t !#-[]'
    mimeCharset='[-!#-&+\dA-Z^-z]+'
    language='(?:[A-Za-z]{2,3}(?:-[A-Za-z]{3}(?:-[A-Za-z]{3}){,2})?|[A-Za-z]{4,8})(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|\d{3}))(?:-(?:[\dA-Za-z]{5,8}|\d[\dA-Za-z]{3}))*(?:-[\dA-WY-Za-wy-z](?:-[\dA-Za-z]{2,8})+)*(?:-[Xx](?:-[\dA-Za-z]{1,8})+)?|[Xx](?:-[\dA-Za-z]{1,8})+|[Ee][Nn]-[Gg][Bb]-[Oo][Ee][Dd]|[Ii]-[Aa][Mm][Ii]|[Ii]-[Bb][Nn][Nn]|[Ii]-[Dd][Ee][Ff][Aa][Uu][Ll][Tt]|[Ii]-[Ee][Nn][Oo][Cc][Hh][Ii][Aa][Nn]|[Ii]-[Hh][Aa][Kk]|[Ii]-[Kk][Ll][Ii][Nn][Gg][Oo][Nn]|[Ii]-[Ll][Uu][Xx]|[Ii]-[Mm][Ii][Nn][Gg][Oo]|[Ii]-[Nn][Aa][Vv][Aa][Jj][Oo]|[Ii]-[Pp][Ww][Nn]|[Ii]-[Tt][Aa][Oo]|[Ii]-[Tt][Aa][Yy]|[Ii]-[Tt][Ss][Uu]|[Ss][Gg][Nn]-[Bb][Ee]-[Ff][Rr]|[Ss][Gg][Nn]-[Bb][Ee]-[Nn][Ll]|[Ss][Gg][Nn]-[Cc][Hh]-[Dd][Ee]'
    valueChars = '(?:%[\dA-F][\dA-F]|[-!#$&+.\dA-Z^-z|~])*'
    dispositionParm = '[Ff][Ii][Ll][Ee][Nn][Aa][Mm][Ee]\s*=\s*(?:({token})|"((?:{qdtext}|\\\\[\t !-~])*)")|[Ff][Ii][Ll][Ee][Nn][Aa][Mm][Ee]\*\s*=\s*({mimeCharset})\'(?:{language})?\'({valueChars})|{token}\s*=\s*(?:{token}|"(?:{qdtext}|\\\\[\t !-~])*")|{token}\*\s*=\s*{mimeCharset}\'(?:{language})?\'{valueChars}'.format(**locals())

    m = re.match('(?:{token}\s*;\s*)?(?:{dispositionParm})(?:\s*;\s*(?:{dispositionParm}))*|{token}'.format(**locals()), content_disposition)
    if not m:
        return

    # Many user agent implementations predating this specification do not
    # understand the "filename*" parameter.  Therefore, when both "filename"
    # and "filename*" are present in a single header field value, recipients
    # SHOULD pick "filename*" and ignore "filename"

    if m.group(8) is not None:
        name = urllib.unquote(m.group(8)).decode(m.group(7))
    elif m.group(4) is not None:
        name = urllib.unquote(m.group(4)).decode(m.group(3))
    elif m.group(6) is not None:
        name = re.sub('\\\\(.)', '\1', m.group(6))
    elif m.group(5) is not None:
        name = m.group(5)
    elif m.group(2) is not None:
        name = re.sub('\\\\(.)', '\1', m.group(2))
    else:
        name = m.group(1)

    # Recipients MUST NOT be able to write into any location other than one to
    # which they are specifically entitled

    return path.basename(name)
