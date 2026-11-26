using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace BaModProbe
{
    /// <summary>
    /// Minimal, dependency-free JSON writer.
    ///
    /// Hand-rolled on purpose, for two independent reasons:
    ///   1. Dump-contract hard rule #3 says values are emitted VERBATIM (no unit conversion,
    ///      no rounding, no dropped fields). Number formatting therefore has to be under our
    ///      control, and float/double go out via the shortest round-trippable form.
    ///   2. Pulling Newtonsoft.Json into a MelonLoader mod risks the reference resolving against
    ///      the game's own Il2CppNewtonsoft.Json proxy inside MelonLoader's load context.
    /// </summary>
    internal sealed class JsonWriter
    {
        private readonly StringBuilder _sb;
        private readonly string _indent;
        private readonly List<bool> _first = new List<bool>();
        private bool _afterName;

        internal JsonWriter(StringBuilder sb, string indent)
        {
            _sb = sb ?? throw new ArgumentNullException(nameof(sb));
            _indent = indent; // null/empty => compact
        }

        private void NewLine(int depth)
        {
            if (string.IsNullOrEmpty(_indent)) return;
            _sb.Append('\n');
            for (int i = 0; i < depth; i++) _sb.Append(_indent);
        }

        private void BeforeValue()
        {
            if (_afterName) { _afterName = false; return; }
            if (_first.Count == 0) return;
            int top = _first.Count - 1;
            if (_first[top]) _first[top] = false;
            else _sb.Append(',');
            NewLine(_first.Count);
        }

        private void Close(char c)
        {
            if (_first.Count > 0) _first.RemoveAt(_first.Count - 1);
            NewLine(_first.Count);
            _sb.Append(c);
        }

        internal void BeginObject() { BeforeValue(); _sb.Append('{'); _first.Add(true); }
        internal void EndObject() { Close('}'); }
        internal void BeginArray() { BeforeValue(); _sb.Append('['); _first.Add(true); }
        internal void EndArray() { Close(']'); }

        internal void Name(string name)
        {
            BeforeValue();
            WriteEscaped(name);
            _sb.Append(':');
            if (!string.IsNullOrEmpty(_indent)) _sb.Append(' ');
            _afterName = true;
        }

        internal void Null() { BeforeValue(); _sb.Append("null"); }

        internal void Bool(bool v) { BeforeValue(); _sb.Append(v ? "true" : "false"); }

        internal void Int(long v) { BeforeValue(); _sb.Append(v.ToString(CultureInfo.InvariantCulture)); }

        internal void Float(float v)
        {
            // JSON has no NaN/Infinity: emit null and let the caller record it.
            if (float.IsNaN(v) || float.IsInfinity(v)) { Null(); return; }
            BeforeValue();
            _sb.Append(FloatText(v.ToString("R", CultureInfo.InvariantCulture)));
        }

        internal void Double(double v)
        {
            if (double.IsNaN(v) || double.IsInfinity(v)) { Null(); return; }
            BeforeValue();
            _sb.Append(FloatText(v.ToString("R", CultureInfo.InvariantCulture)));
        }

        /// <summary>
        /// Make an integral floating value look like a float.
        ///
        /// Measured fact, not a guess: in DataBaseCompiled/&lt;ver&gt;/*.json EVERY System.Single
        /// column is tokenised with a decimal point -- "Damage":10.0, "MuzzleVelocity":550.0,
        /// "MinimalRange":0.0 -- 51,085 values audited across all 24 tables with zero exceptions,
        /// while System.Int32 columns are never tokenised that way (142,799 values).
        /// Emitting "10" for a float column would therefore surface as a spurious difference in
        /// the offline comparator. See _work/number_token_audit.py for the measurement.
        /// </summary>
        private static string FloatText(string r)
        {
            for (int i = 0; i < r.Length; i++)
            {
                char c = r[i];
                if (c == '.' || c == 'e' || c == 'E') return r;
            }
            return r + ".0";
        }

        internal void Str(string v)
        {
            if (v == null) { Null(); return; }
            BeforeValue();
            WriteEscaped(v);
        }

        private void WriteEscaped(string s)
        {
            _sb.Append('"');
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                switch (c)
                {
                    case '"': _sb.Append("\\\""); break;
                    case '\\': _sb.Append("\\\\"); break;
                    case '\b': _sb.Append("\\b"); break;
                    case '\f': _sb.Append("\\f"); break;
                    case '\n': _sb.Append("\\n"); break;
                    case '\r': _sb.Append("\\r"); break;
                    case '\t': _sb.Append("\\t"); break;
                    default:
                        if (c < ' ')
                        {
                            _sb.Append("\\u");
                            _sb.Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        }
                        else
                        {
                            _sb.Append(c);
                        }
                        break;
                }
            }
            _sb.Append('"');
        }
    }

    /// <summary>
    /// Minimal strict JSON reader. Produces:
    ///   object                 -> Dictionary&lt;string, object&gt; (ordinal, insertion ordered)
    ///   array                  -> List&lt;object&gt;
    ///   string                 -> string
    ///   integral number        -> long   (kept integral so ids never lose precision)
    ///   other number           -> double
    ///   true / false           -> bool
    ///   null                   -> null
    /// Callers convert to the target property type themselves (see DbOverride.ConvertTo).
    /// </summary>
    internal static class JsonReader
    {
        internal static object Parse(string text)
        {
            if (text == null) throw new FormatException("JSON text is null");
            int i = 0;
            object v = ParseValue(text, ref i);
            SkipWs(text, ref i);
            if (i != text.Length) throw new FormatException("trailing content at offset " + i);
            return v;
        }

        private static void SkipWs(string s, ref int i)
        {
            while (i < s.Length)
            {
                char c = s[i];
                if (c == ' ' || c == '\t' || c == '\r' || c == '\n') { i++; continue; }
                break;
            }
        }

        private static object ParseValue(string s, ref int i)
        {
            SkipWs(s, ref i);
            if (i >= s.Length) throw new FormatException("unexpected end of JSON");
            char c = s[i];
            switch (c)
            {
                case '{': return ParseObject(s, ref i);
                case '[': return ParseArray(s, ref i);
                case '"': return ParseString(s, ref i);
                case 't': Expect(s, ref i, "true"); return true;
                case 'f': Expect(s, ref i, "false"); return false;
                case 'n': Expect(s, ref i, "null"); return null;
                default: return ParseNumber(s, ref i);
            }
        }

        private static void Expect(string s, ref int i, string word)
        {
            if (i + word.Length > s.Length || string.CompareOrdinal(s, i, word, 0, word.Length) != 0)
                throw new FormatException("expected '" + word + "' at offset " + i);
            i += word.Length;
        }

        private static Dictionary<string, object> ParseObject(string s, ref int i)
        {
            var d = new Dictionary<string, object>(StringComparer.Ordinal);
            i++; // '{'
            SkipWs(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; return d; }
            while (true)
            {
                SkipWs(s, ref i);
                if (i >= s.Length || s[i] != '"') throw new FormatException("expected object key at offset " + i);
                string k = ParseString(s, ref i);
                SkipWs(s, ref i);
                if (i >= s.Length || s[i] != ':') throw new FormatException("expected ':' at offset " + i);
                i++;
                d[k] = ParseValue(s, ref i);
                SkipWs(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated object");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == '}') { i++; return d; }
                throw new FormatException("expected ',' or '}' at offset " + i);
            }
        }

        private static List<object> ParseArray(string s, ref int i)
        {
            var list = new List<object>();
            i++; // '['
            SkipWs(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; return list; }
            while (true)
            {
                list.Add(ParseValue(s, ref i));
                SkipWs(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated array");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == ']') { i++; return list; }
                throw new FormatException("expected ',' or ']' at offset " + i);
            }
        }

        private static string ParseString(string s, ref int i)
        {
            i++; // opening quote
            var sb = new StringBuilder();
            while (true)
            {
                if (i >= s.Length) throw new FormatException("unterminated string");
                char c = s[i++];
                if (c == '"') return sb.ToString();
                if (c != '\\') { sb.Append(c); continue; }
                if (i >= s.Length) throw new FormatException("unterminated escape");
                char e = s[i++];
                switch (e)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (i + 4 > s.Length) throw new FormatException("bad \\u escape");
                        sb.Append((char)ushort.Parse(s.Substring(i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture));
                        i += 4;
                        break;
                    default: throw new FormatException("bad escape '\\" + e + "'");
                }
            }
        }

        // Returns object, not double: an integral token must stay a long. With a double return
        // type the compiler silently widens it and every id/counter comes back as a double.
        private static object ParseNumber(string s, ref int i)
        {
            int start = i;
            if (i < s.Length && (s[i] == '-' || s[i] == '+')) i++;
            while (i < s.Length)
            {
                char c = s[i];
                if ((c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') { i++; continue; }
                break;
            }
            string tok = s.Substring(start, i - start);
            if (tok.Length == 0) throw new FormatException("expected a value at offset " + start);
            if (tok.IndexOf('.') < 0 && tok.IndexOf('e') < 0 && tok.IndexOf('E') < 0)
            {
                long l;
                if (long.TryParse(tok, NumberStyles.Integer, CultureInfo.InvariantCulture, out l)) return l;
            }
            double d;
            if (!double.TryParse(tok, NumberStyles.Float, CultureInfo.InvariantCulture, out d))
                throw new FormatException("bad number '" + tok + "'");
            return d;
        }
    }
}
