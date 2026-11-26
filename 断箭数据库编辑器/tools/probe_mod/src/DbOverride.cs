using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace BaModProbe
{
    /// <summary>
    /// Task 2 -- sparse delta override, debug only.
    ///
    /// Input: &lt;GameDir&gt;\UserData\BaModProbe\override.json
    ///     { "Ammunitions": { "123": { "MuzzleVelocity": 1800, "Damage": 45 } },
    ///       "Armors":      { "70":  { "KinArmorFront": 500 } } }
    /// i.e. table -&gt; row Id (string) -&gt; column -&gt; new value.
    ///
    /// Every write goes through WriteJournal, which captures the previous value first, so a
    /// single static ProbeMod.Restore() puts all of it back. The run report distinguishes the
    /// four ways a requested cell can fail to land:
    ///     row not found / column not found / column read-only / value conversion failed
    /// </summary>
    internal static class DbOverride
    {
        internal sealed class Report
        {
            internal int TablesRequested;
            internal int RowsRequested;
            internal int CellsRequested;
            internal int CellsWritten;
            internal int RowsNotFound;
            internal int ColumnsNotFound;
            internal int ColumnsReadOnly;
            internal int CellsFailed;
            internal readonly List<string> Applied = new List<string>();
            internal readonly List<string> Problems = new List<string>();

            internal string Text()
            {
                var sb = new StringBuilder();
                sb.Append("override: tables=").Append(TablesRequested)
                  .Append(" rows=").Append(RowsRequested)
                  .Append(" cells=").Append(CellsRequested)
                  .Append(" written=").Append(CellsWritten)
                  .Append(" row-not-found=").Append(RowsNotFound)
                  .Append(" col-not-found=").Append(ColumnsNotFound)
                  .Append(" col-read-only=").Append(ColumnsReadOnly)
                  .Append(" conversion-failed=").Append(CellsFailed);
                return sb.ToString();
            }
        }

        internal static Report Apply(object source, string trigger)
        {
            var rep = new Report();
            if (source == null) { rep.Problems.Add("no database source"); return rep; }

            if (!File.Exists(ProbePaths.OverrideFile))
            {
                ProbeLog.Info("override: no " + ProbePaths.OverrideFile + " -- nothing to do");
                return rep;
            }

            Dictionary<string, object> root;
            try
            {
                root = JsonReader.Parse(File.ReadAllText(ProbePaths.OverrideFile, Encoding.UTF8))
                       as Dictionary<string, object>;
            }
            catch (Exception ex)
            {
                ProbeLog.Error("override: cannot parse " + ProbePaths.OverrideFile, ex);
                rep.Problems.Add("parse error: " + ex.Message);
                return rep;
            }
            if (root == null) { rep.Problems.Add("override.json top level is not an object"); return rep; }

            Type srcType = source.GetType();

            foreach (KeyValuePair<string, object> tableKv in root)
            {
                string table = tableKv.Key;
                var rowsSpec = tableKv.Value as Dictionary<string, object>;
                if (rowsSpec == null)
                {
                    rep.Problems.Add(table + ": expected an object of { \"<id>\": { <column>: <value> } }");
                    continue;
                }
                rep.TablesRequested++;

                List<object> rows;
                string err;
                if (!DbDump.TryGetRows(source, table, out rows, out err))
                {
                    rep.Problems.Add(table + ": " + err);
                    ProbeLog.Warn("override: table " + table + " unreadable -- " + err);
                    continue;
                }

                // Index by Id. Enumerating GetAll() into a managed dictionary keeps us clear of
                // the Il2Cpp proxy collection types entirely.
                var byId = new Dictionary<int, object>();
                foreach (object r in rows)
                {
                    int rid = RowAccess.ReadId(r);
                    if (rid != int.MinValue) byId[rid] = r;
                }

                foreach (KeyValuePair<string, object> rowKv in rowsSpec)
                {
                    int id;
                    if (!int.TryParse(rowKv.Key, NumberStyles.Integer, CultureInfo.InvariantCulture, out id))
                    {
                        rep.Problems.Add(table + ": '" + rowKv.Key + "' is not a row Id");
                        continue;
                    }
                    rep.RowsRequested++;

                    object row;
                    if (!byId.TryGetValue(id, out row))
                    {
                        rep.RowsNotFound++;
                        rep.Problems.Add(table + "[" + id + "]: row not found (table has " + byId.Count + " rows)");
                        continue;
                    }

                    var colsSpec = rowKv.Value as Dictionary<string, object>;
                    if (colsSpec == null)
                    {
                        rep.Problems.Add(table + "[" + id + "]: expected { <column>: <value> }");
                        continue;
                    }

                    Type rowType = row.GetType();
                    foreach (KeyValuePair<string, object> colKv in colsSpec)
                    {
                        rep.CellsRequested++;
                        string col = colKv.Key;
                        ColumnHandle h = RowAccess.Resolve(rowType, col);
                        if (h == null)
                        {
                            rep.ColumnsNotFound++;
                            rep.Problems.Add(table + "[" + id + "]." + col + ": column not found on " + rowType.FullName);
                            continue;
                        }
                        if (!h.CanWrite)
                        {
                            rep.ColumnsReadOnly++;
                            rep.Problems.Add(table + "[" + id + "]." + col + ": read-only (no public setter in Il2CppBrokenArrow)");
                            continue;
                        }

                        object value;
                        string cerr;
                        if (!ConvertTo(h.ValueType, colKv.Value, out value, out cerr))
                        {
                            rep.CellsFailed++;
                            rep.Problems.Add(table + "[" + id + "]." + col + ": " + cerr);
                            continue;
                        }

                        try
                        {
                            object before = WriteJournal.WriteAndRecord(row, h, value, table, id);
                            rep.CellsWritten++;
                            rep.Applied.Add(table + "[" + id + "]." + col + ": "
                                + Format(before) + " -> " + Format(value));
                        }
                        catch (Exception ex)
                        {
                            rep.CellsFailed++;
                            rep.Problems.Add(table + "[" + id + "]." + col + ": write threw " + ex.Message);
                            ProbeLog.Error("override: write failed " + table + "[" + id + "]." + col, ex);
                        }
                    }
                }
            }

            ProbeLog.Info("override (" + trigger + "): " + rep.Text());
            foreach (string a in rep.Applied) ProbeLog.Info("  applied  " + a);
            foreach (string p in rep.Problems) ProbeLog.Warn("  problem  " + p);
            return rep;
        }

        private static string Format(object v)
        {
            if (v == null) return "null";
            return Convert.ToString(v, CultureInfo.InvariantCulture);
        }

        /// <summary>Coerce a parsed JSON value onto a target CLR type.</summary>
        internal static bool ConvertTo(Type target, object raw, out object value, out string error)
        {
            value = null;
            error = null;
            Type underlying = Nullable.GetUnderlyingType(target);
            Type u = underlying ?? target;
            bool nullable = underlying != null;

            if (raw == null)
            {
                if (nullable) return true;
                error = "null is not valid for " + u.Name;
                return false;
            }

            try
            {
                if (u.IsEnum)
                {
                    var s = raw as string;
                    if (s != null)
                    {
                        if (!Enum.IsDefined(u, s)) { error = "'" + s + "' is not a member of " + u.FullName; return false; }
                        value = Enum.Parse(u, s, false);
                        return true;
                    }
                    long n = ToLong(raw);
                    value = Enum.ToObject(u, n);
                    return true;
                }
                if (u == typeof(bool))
                {
                    if (raw is bool) { value = raw; return true; }
                    var s = raw as string;
                    if (s != null)
                    {
                        bool b;
                        if (bool.TryParse(s, out b)) { value = b; return true; }
                        error = "'" + s + "' is not a boolean";
                        return false;
                    }
                    value = ToDouble(raw) != 0.0;
                    return true;
                }
                if (u == typeof(string)) { value = raw as string ?? Convert.ToString(raw, CultureInfo.InvariantCulture); return true; }
                if (u == typeof(float))  { value = Convert.ToSingle(ToDouble(raw), CultureInfo.InvariantCulture); return true; }
                if (u == typeof(double)) { value = ToDouble(raw); return true; }
                if (u == typeof(decimal)){ value = Convert.ToDecimal(ToDouble(raw), CultureInfo.InvariantCulture); return true; }
                if (u == typeof(byte))   { value = checked((byte)ToLong(raw)); return true; }
                if (u == typeof(sbyte))  { value = checked((sbyte)ToLong(raw)); return true; }
                if (u == typeof(short))  { value = checked((short)ToLong(raw)); return true; }
                if (u == typeof(ushort)) { value = checked((ushort)ToLong(raw)); return true; }
                if (u == typeof(int))    { value = checked((int)ToLong(raw)); return true; }
                if (u == typeof(uint))   { value = checked((uint)ToLong(raw)); return true; }
                if (u == typeof(long))   { value = ToLong(raw); return true; }

                error = "unsupported target type " + target.FullName;
                return false;
            }
            catch (OverflowException)
            {
                error = "value " + Format(raw) + " overflows " + u.Name;
                return false;
            }
            catch (Exception ex)
            {
                error = ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        private static long ToLong(object raw)
        {
            if (raw is long) return (long)raw;
            if (raw is double) return checked((long)Math.Round((double)raw, MidpointRounding.AwayFromZero));
            if (raw is bool) return ((bool)raw) ? 1L : 0L;
            var s = raw as string;
            if (s != null) return long.Parse(s, NumberStyles.Integer, CultureInfo.InvariantCulture);
            return Convert.ToInt64(raw, CultureInfo.InvariantCulture);
        }

        private static double ToDouble(object raw)
        {
            if (raw is double) return (double)raw;
            if (raw is long) return (long)raw;
            if (raw is bool) return ((bool)raw) ? 1.0 : 0.0;
            var s = raw as string;
            if (s != null) return double.Parse(s, NumberStyles.Float, CultureInfo.InvariantCulture);
            return Convert.ToDouble(raw, CultureInfo.InvariantCulture);
        }
    }
}
