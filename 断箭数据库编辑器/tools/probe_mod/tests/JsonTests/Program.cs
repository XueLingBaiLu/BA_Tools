using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using BaModProbe;

namespace BaModProbe.Tests
{
    /// <summary>
    /// Offline tests for the hand-rolled JSON layer (src/Json.cs). That file is the only part
    /// of the probe with no game or MelonLoader dependency, so it is the only part that can be
    /// exercised without launching the game -- and it is also the part most likely to be wrong.
    ///
    /// Run:  dotnet run --project tests/JsonTests
    /// Exit: 0 = every assertion held; 1 = a failure (printed)
    /// </summary>
    internal static class Program
    {
        private static int _pass;
        private static readonly List<string> _fail = new List<string>();

        private static void Check(bool ok, string what)
        {
            if (ok) _pass++;
            else _fail.Add(what);
        }

        private static void Eq(object expected, object actual, string what)
        {
            bool ok = expected == null ? actual == null : expected.Equals(actual);
            if (ok) _pass++;
            else _fail.Add(what + "  expected=<" + Fmt(expected) + "> actual=<" + Fmt(actual) + ">");
        }

        private static string Fmt(object o)
        {
            if (o == null) return "null";
            var s = o as string;
            return s ?? Convert.ToString(o, CultureInfo.InvariantCulture);
        }

        private static string Write(Action<JsonWriter> body, bool indented)
        {
            var sb = new StringBuilder();
            var w = new JsonWriter(sb, indented ? " " : null);
            body(w);
            return sb.ToString();
        }

        private static int Main()
        {
            // ---- 1. numbers must survive verbatim (dump-contract hard rule #2) -------------
            Eq("0.375", Write(w => w.Float(0.375f), false), "float 0.375 verbatim");
            // Measured against DataBaseCompiled/52085: every System.Single column is tokenised
            // with a decimal point, so an integral float must NOT come out looking like an int.
            Eq("1.0", Write(w => w.Float(1.0f), false), "integral float keeps its decimal point");
            Eq("0.0", Write(w => w.Float(0f), false), "float zero is 0.0");
            Eq("10.0", Write(w => w.Float(10f), false), "float ten is 10.0");
            Eq("-1.0", Write(w => w.Float(-1f), false), "negative integral float is -1.0");
            Eq("550.0", Write(w => w.Float(550f), false), "MuzzleVelocity-shaped value is 550.0");
            Eq("1.5", Write(w => w.Float(1.5f), false), "fractional float unchanged");
            Eq("1.0", Write(w => w.Double(1.0d), false), "integral double keeps its decimal point");
            Eq("2.5", Write(w => w.Double(2.5d), false), "fractional double unchanged");
            Check(Write(w => w.Float(1f), false) != "1",
                  "POSITIVE CONTROL: an integral float must never be tokenised as an int");
            Eq("1800", Write(w => w.Int(1800L), false), "int verbatim");
            Eq("-1", Write(w => w.Int(-1L), false), "negative enum value verbatim");
            Eq("null", Write(w => w.Float(float.NaN), false), "NaN -> null (JSON has no NaN)");
            Eq("null", Write(w => w.Float(float.PositiveInfinity), false), "Infinity -> null");
            Eq("null", Write(w => w.Double(double.NaN), false), "double NaN -> null");
            // a float must round-trip through the emitted text without loss
            foreach (float f in new[] { 0.1f, 1f / 3f, 12345.678f, -0.000123f, 3.4e38f, 1e-45f })
            {
                string t = Write(w => w.Float(f), false);
                float roundTripped = float.Parse(t, CultureInfo.InvariantCulture);
                Check(roundTripped.Equals(f), "float round-trip " + f.ToString("R", CultureInfo.InvariantCulture) + " -> " + t);
            }

            // ---- 2. string escaping --------------------------------------------------------
            Eq("\"a\\\"b\"", Write(w => w.Str("a\"b"), false), "escape double quote");
            Eq("\"a\\\\b\"", Write(w => w.Str("a\\b"), false), "escape backslash");
            Eq("\"a\\nb\"", Write(w => w.Str("a\nb"), false), "escape newline");
            Eq("\"a\\tb\"", Write(w => w.Str("a\tb"), false), "escape tab");
            Eq("\"\\u0001\"", Write(w => w.Str("\u0001"), false), "escape control char as \\uXXXX");
            Eq("\"\\f\"", Write(w => w.Str("\f"), false), "form feed uses the short JSON escape");
            Eq("\"\"", Write(w => w.Str(""), false), "empty string");
            Eq("null", Write(w => w.Str(null), false), "null string -> null");
            Eq("\"\\u4e2d\\u6587\"".Replace("\\u4e2d\\u6587", "\u4e2d\u6587"),
               Write(w => w.Str("\u4e2d\u6587"), false), "non-ASCII passes through raw, not escaped");

            // ---- 3. structure: commas and nesting -----------------------------------------
            string nested = Write(w =>
            {
                w.BeginObject();
                w.Name("meta"); w.BeginObject(); w.Name("table_count"); w.Int(24); w.EndObject();
                w.Name("tables"); w.BeginArray(); w.Int(1); w.Int(2); w.Int(3); w.EndArray();
                w.Name("empty"); w.BeginArray(); w.EndArray();
                w.EndObject();
            }, false);
            Eq("{\"meta\":{\"table_count\":24},\"tables\":[1,2,3],\"empty\":[]}", nested, "compact nesting and commas");

            string indented = Write(w =>
            {
                w.BeginObject();
                w.Name("a"); w.Int(1);
                w.Name("b"); w.BeginArray(); w.Int(2); w.EndArray();
                w.EndObject();
            }, true);
            Check(indented.IndexOf('\n') > 0, "indented mode emits newlines");
            // every bracket must balance and the text must be parseable
            Check(Balanced(indented), "indented output has balanced brackets");

            // ---- 4. reader ----------------------------------------------------------------
            Eq(24L, JsonReader.Parse("24"), "reader: integral token stays long");
            Eq(-1L, JsonReader.Parse("-1"), "reader: negative integral stays long");
            Eq(1.5d, JsonReader.Parse("1.5"), "reader: fractional becomes double");
            Eq(2.5e10d, JsonReader.Parse("2.5e10"), "reader: exponent becomes double");
            Eq(true, JsonReader.Parse("true"), "reader: true");
            Eq(false, JsonReader.Parse("false"), "reader: false");
            Check(JsonReader.Parse("null") == null, "reader: null");
            Eq("x", JsonReader.Parse("\"x\""), "reader: bare string");
            Eq("a\"b\\c\nd", JsonReader.Parse("\"a\\\"b\\\\c\\nd\""), "reader: escapes");
            Eq("\u4e2d", JsonReader.Parse("\"\\u4e2d\""), "reader: \\u escape");
            Eq(" ", JsonReader.Parse("\"\\u0020\""), "reader: \\u0020");

            var obj = JsonReader.Parse("{\"A\":{\"1\":{\"Damage\":45}},\"B\":[1,2]}") as Dictionary<string, object>;
            Check(obj != null && obj.Count == 2, "reader: object with 2 keys");
            var inner = obj["A"] as Dictionary<string, object>;
            Check(inner != null && inner.ContainsKey("1"), "reader: nested object key preserved");
            var leaf = inner["1"] as Dictionary<string, object>;
            Eq(45L, leaf["Damage"], "reader: nested leaf value");
            Check((obj["B"] as List<object>).Count == 2, "reader: array of 2");

            // ---- 5. writer -> reader round trip on a dump-shaped document ------------------
            string doc = Write(w =>
            {
                w.BeginObject();
                w.Name("meta");
                w.BeginObject();
                w.Name("generated_utc"); w.Str("2026-10-10T00:00:00Z");
                w.Name("table_count"); w.Int(24);
                w.EndObject();
                w.Name("tables");
                w.BeginObject();
                w.Name("Ammunitions");
                w.BeginArray();
                for (int i = 0; i < 3; i++)
                {
                    w.BeginObject();
                    w.Name("Id"); w.Int(i + 1);
                    w.Name("Name"); w.Str("ammo \"quoted\" " + i);
                    w.Name("Damage"); w.Float(45.5f);
                    w.Name("CanBeIntercepted"); w.Bool(i % 2 == 0);
                    w.Name("ParentTurretId"); w.Null();
                    w.Name("Role"); w.Int(-1);
                    w.EndObject();
                }
                w.EndArray();
                w.EndObject();
                w.EndObject();
            }, true);

            var back = JsonReader.Parse(doc) as Dictionary<string, object>;
            Check(back != null, "round-trip: top level object");
            var m = back["meta"] as Dictionary<string, object>;
            Eq(24L, m["table_count"], "round-trip: meta.table_count");
            var tables = back["tables"] as Dictionary<string, object>;
            var ammo = tables["Ammunitions"] as List<object>;
            Check(ammo != null && ammo.Count == 3, "round-trip: Ammunitions has 3 rows");
            var r0 = ammo[0] as Dictionary<string, object>;
            Eq(1L, r0["Id"], "round-trip: row Id");
            Eq("ammo \"quoted\" 0", r0["Name"], "round-trip: quoted string survives");
            Eq(45.5d, r0["Damage"], "round-trip: float value");
            Eq(true, r0["CanBeIntercepted"], "round-trip: bool");
            Check(r0["ParentTurretId"] == null, "round-trip: null");
            Eq(-1L, r0["Role"], "round-trip: negative enum as number");

            // ---- 6. POSITIVE CONTROL: the checks above must be able to go red -------------
            int before = _fail.Count;
            Eq("WRONG", Write(w => w.Float(0.375f), false), "POSITIVE CONTROL (this line is expected to fail)");
            Check(_fail.Count == before + 1, "positive control: a wrong expectation is detected");
            if (_fail.Count > 0 && _fail[_fail.Count - 1].StartsWith("POSITIVE CONTROL"))
                _fail.RemoveAt(_fail.Count - 1);

            // ---- 7. Il2CppSystem.Nullable<T> unwrapping --------------------------------
            // The real proxy type is a CLASS named Il2CppSystem.Nullable`1, not System.Nullable<T>,
            // so System.Nullable.GetUnderlyingType does not see it, and Turrets.ParentTurretId used to
            // be written as null even when it had a value. See src/Il2CppNullable.cs.
            {
            object nbInner;

            var withValue = new Il2CppSystem.Nullable<int>(true, 89);
            Check(Il2CppNullable.TryUnwrap(withValue, out nbInner), "nullable: value-bearing instance is recognised");
            Eq(89, nbInner, "nullable: payload comes back (89)");

            var noValue = new Il2CppSystem.Nullable<int>(false, 0);
            Check(Il2CppNullable.TryUnwrap(noValue, out nbInner), "nullable: absent-value instance is recognised");
            Check(nbInner == null, "nullable: absent value unwraps to null, NOT to 0");

            var upperOnly = new Il2CppSystem.NullableUpperOnly<int>(true, 7);
            Check(Il2CppNullable.TryUnwrap(upperOnly, out nbInner), "nullable: HasValue/Value-only shape is recognised");
            Eq(7, nbInner, "nullable: upper-case-only payload comes back (7)");

            Check(!Il2CppNullable.TryUnwrap(new Il2CppSystem.NullableNoAccessor<int>(), out nbInner),
                  "nullable: shape without a has-value accessor is refused");
            Check(!Il2CppNullable.TryUnwrap(new Il2CppSystem.Nullable(), out nbInner),
                  "nullable: non-generic decoy is refused");
            Check(!Il2CppNullable.TryUnwrap(new NotIl2CppSystem.Nullable<int>(true, 1), out nbInner),
                  "nullable: right name in the WRONG namespace is refused");

            int? realSystemNullable = 5;
            Check(!Il2CppNullable.TryUnwrap((object)realSystemNullable, out nbInner),
                  "nullable: real System.Nullable<T> is left to Nullable.GetUnderlyingType");
            Check(!Il2CppNullable.TryUnwrap(null, out nbInner), "nullable: null input is refused");
            Check(!Il2CppNullable.TryUnwrap("a string", out nbInner), "nullable: an unrelated object is refused");

            int beforeNullable = _fail.Count;
            Eq(999, nbInner, "POSITIVE CONTROL nullable (expected to fail)");
            Check(_fail.Count == beforeNullable + 1, "positive control: a wrong nullable payload is detected");
            if (_fail.Count > 0 && _fail[_fail.Count - 1].StartsWith("POSITIVE CONTROL"))
                _fail.RemoveAt(_fail.Count - 1);

            }

            Eq(typeof(int), Il2CppNullable.UnderlyingOf(typeof(Il2CppSystem.Nullable<int>)),
               "nullable-type: UnderlyingOf(proxy Nullable<int>) is int");
            Eq(typeof(string), Il2CppNullable.UnderlyingOf(typeof(Il2CppSystem.Nullable<string>)),
               "nullable-type: UnderlyingOf(proxy Nullable<string>) is string");
            Check(Il2CppNullable.IsProxyNullable(typeof(Il2CppSystem.Nullable<int>)),
                  "nullable-type: proxy Nullable<int> IS recognised");
            Check(!Il2CppNullable.IsProxyNullable(typeof(int?)),
                  "nullable-type: real System.Nullable<int> is NOT a proxy nullable");
            Check(!Il2CppNullable.IsProxyNullable(typeof(NotIl2CppSystem.Nullable<int>)),
                  "nullable-type: wrong namespace is NOT a proxy nullable");
            Check(!Il2CppNullable.IsProxyNullable(typeof(string)),
                  "nullable-type: a plain type is NOT a proxy nullable");
            Check(Il2CppNullable.UnderlyingOf(typeof(Il2CppSystem.Nullable)) == null,
                  "nullable-type: the non-generic decoy has no underlying type");
            // This is the predicate that made Turrets.ParentTurretId be treated as a navigation
            // reference, so the row loop replaced it with null before WriteValue could unwrap it.
            Check(!Il2CppNullable.IsProxyNullable(typeof(Il2CppSystem.NullableNoAccessor<int>)) ||
                  Il2CppNullable.UnderlyingOf(typeof(Il2CppSystem.NullableNoAccessor<int>)) == typeof(int),
                  "nullable-type: prefixed generic types resolve their payload");

            // Measure, do not assume: report the CLR type every reader branch actually produces.
            Console.WriteLine("reader runtime types: "
                + "24->" + JsonReader.Parse("24").GetType().Name
                + "  -1->" + JsonReader.Parse("-1").GetType().Name
                + "  1.5->" + JsonReader.Parse("1.5").GetType().Name
                + "  45->" + JsonReader.Parse("45").GetType().Name);
            Console.WriteLine("JsonTests: pass=" + _pass + " fail=" + _fail.Count);
            foreach (string f in _fail) Console.WriteLine("  FAIL " + f);
            Console.WriteLine(_fail.Count == 0 ? "RESULT: ALL JSON ASSERTIONS HELD" : "RESULT: FAILURES");
            return _fail.Count == 0 ? 0 : 1;
        }

        private static bool Balanced(string s)
        {
            int depth = 0;
            bool inStr = false;
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (inStr)
                {
                    if (c == '\\') { i++; continue; }
                    if (c == '"') inStr = false;
                    continue;
                }
                if (c == '"') { inStr = true; continue; }
                if (c == '{' || c == '[') depth++;
                else if (c == '}' || c == ']') { depth--; if (depth < 0) return false; }
            }
            return depth == 0 && !inStr;
        }
    }
}
