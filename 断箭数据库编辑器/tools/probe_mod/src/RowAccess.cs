using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using Il2CppInterop.Runtime;
using Il2CppInterop.Runtime.InteropTypes;

namespace BaModProbe
{
    /// <summary>How a column name was resolved on a row type.</summary>
    internal enum MemberKind { None, Property, BackingFieldProperty, Field, InterfaceProperty }

    /// <summary>A resolved read/write handle for one column of one row type.</summary>
    internal sealed class ColumnHandle
    {
        internal string Column;
        internal MemberKind Kind;
        internal PropertyInfo Prop;      // set when Kind is Property / BackingFieldProperty / InterfaceProperty
        internal PropertyInfo BackingProp; // "_<name>_k__BackingField" twin, when the model has one
        internal FieldInfo Field;        // set when Kind is Field
        internal Type ValueType;         // the declared type carrying the value
        internal bool CanWrite;

        /// <summary>True when the model has no stored field for this column, i.e. the game derives it.</summary>
        internal bool IsComputed { get { return BackingProp == null && Prop != null; } }

        internal object Get(object row)
        {
            if (Prop != null)
            {
                try
                {
                    return Prop.GetValue(row);
                }
                catch (Exception) when (BackingProp != null && !ReferenceEquals(BackingProp, Prop))
                {
                    // The public getter runs game code through IL2CPP::il2cpp_runtime_invoke and
                    // can throw; the backing-field getter instead does
                    // il2cpp_field_get_offset + il2cpp_value_box and cannot. Measured case:
                    // Turrets.ParentTurretId declares Il2CppSystem.Nullable<int> and its public
                    // getter raised for all 965 rows whose value is absent, which is what produced
                    // 965 "read Turrets.ParentTurretId threw" warnings on the first good run.
                    // Fall through to the backing field and read the real value (absent -> null).
                }
            }
            if (BackingProp != null && !ReferenceEquals(BackingProp, Prop)) return BackingProp.GetValue(row);
            if (Field != null) return Field.GetValue(row);
            return null;
        }

        internal void Set(object row, object value)
        {
            if (Prop != null) Prop.SetValue(row, value);
            else if (Field != null) Field.SetValue(row, value);
            else throw new InvalidOperationException("column '" + Column + "' has no writer");
        }
    }

    /// <summary>
    /// Name-to-member resolution on Il2CppInterop proxy row types.
    ///
    /// The lookup ladder below was derived from the actual proxy assembly, not guessed:
    /// every column also exists as "_&lt;name&gt;_k__BackingField", both with public getters and
    /// setters, and the plain-named property is present too (see _work/proxy_members.json).
    /// Resolution order:
    ///   1. public instance property with the plain name
    ///   2. non-public instance property with the plain name
    ///   3. "_&lt;name&gt;_k__BackingField" property (Il2CppInterop auto-property shape)
    ///   4. public/non-public field with the plain name
    ///   5. interface property (covers C# explicit interface implementations)
    ///   6. anything on a base class
    /// </summary>
    internal static class RowAccess
    {
        internal const BindingFlags PubInst = BindingFlags.Public | BindingFlags.Instance;
        internal const BindingFlags AnyInst = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

        private static readonly Dictionary<string, ColumnHandle> Cache = new Dictionary<string, ColumnHandle>(StringComparer.Ordinal);
        private static readonly object CacheGate = new object();

        internal static ColumnHandle Resolve(Type rowType, string column)
        {
            if (rowType == null || string.IsNullOrEmpty(column)) return null;
            string key = rowType.FullName + "|" + column;
            lock (CacheGate)
            {
                ColumnHandle hit;
                if (Cache.TryGetValue(key, out hit)) return hit;
            }
            ColumnHandle h = ResolveUncached(rowType, column);
            lock (CacheGate) { Cache[key] = h; }
            return h;
        }

        private static ColumnHandle ResolveUncached(Type rowType, string column)
        {
            string backing = "_" + column + "_k__BackingField";
            PropertyInfo named = null, bk = null;
            FieldInfo fld = null;
            for (Type t = rowType; t != null; t = t.BaseType)
            {
                if (named == null) named = t.GetProperty(column, PubInst) ?? t.GetProperty(column, AnyInst);
                if (bk == null) bk = t.GetProperty(backing, AnyInst);
                if (fld == null) fld = t.GetField(column, PubInst) ?? t.GetField(column, AnyInst);
            }

            // Named property first: it is the game's own API, so it is what the game actually uses.
            // The backing-field twin is kept alongside as a rescue path for when that getter throws.
            if (named != null)
            {
                ColumnHandle h = Make(column, named.PropertyType, named, null, MemberKind.Property);
                h.BackingProp = bk;
                return h;
            }
            if (fld != null)
            {
                ColumnHandle h = Make(column, fld.FieldType, null, fld, MemberKind.Field);
                h.BackingProp = bk;
                return h;
            }
            if (bk != null) return Make(column, bk.PropertyType, bk, null, MemberKind.BackingFieldProperty);

            foreach (Type itf in rowType.GetInterfaces())
            {
                PropertyInfo ip = itf.GetProperty(column, PubInst);
                if (ip != null) return Make(column, ip.PropertyType, ip, null, MemberKind.InterfaceProperty);
            }
            return null;
        }

        /// <summary>Read a member by name off an arbitrary object (used for Il2CppSystem.Nullable&lt;T&gt;).</summary>
        internal static bool TryReadMember(object target, string name, out object value)
        {
            value = null;
            if (target == null) return false;
            Type t = target.GetType();
            PropertyInfo p = t.GetProperty(name, PubInst) ?? t.GetProperty(name, AnyInst);
            if (p != null && p.CanRead) { value = p.GetValue(target); return true; }
            FieldInfo f = t.GetField(name, PubInst) ?? t.GetField(name, AnyInst);
            if (f != null) { value = f.GetValue(target); return true; }
            return false;
        }

        private static ColumnHandle Make(string column, Type valueType, PropertyInfo p, FieldInfo f, MemberKind kind)
        {
            var h = new ColumnHandle { Column = column, Kind = kind, Prop = p, Field = f, ValueType = valueType };
            if (p != null) h.CanWrite = p.SetMethod != null && p.SetMethod.IsPublic;
            else if (f != null) h.CanWrite = f.IsPublic && !f.IsInitOnly && !f.IsLiteral;
            return h;
        }

        /// <summary>True when an Il2Cpp proxy value is C# null or wraps a zero pointer.</summary>
        internal static bool IsNull(object value)
        {
            if (value == null) return true;
            var b = value as Il2CppObjectBase;
            if (b != null) return b.Pointer == IntPtr.Zero;
            return false;
        }

        internal static bool IsPrimitiveish(Type t)
        {
            // Il2CppInterop's Nullable<T> is a CLASS, so System.Nullable.GetUnderlyingType misses it
            // and it used to be classified as a navigation reference. Recurse into the real payload.
            Type proxyPayload = Il2CppNullable.UnderlyingOf(t);
            if (proxyPayload != null) return IsPrimitiveish(proxyPayload);

            Type u = Nullable.GetUnderlyingType(t) ?? t;
            return u.IsEnum || u == typeof(bool) || u == typeof(string)
                || u == typeof(byte) || u == typeof(sbyte) || u == typeof(short) || u == typeof(ushort)
                || u == typeof(int) || u == typeof(uint) || u == typeof(long) || u == typeof(ulong)
                || u == typeof(float) || u == typeof(double) || u == typeof(decimal);
        }

        /// <summary>Read the Id of a row through whatever member actually carries it.</summary>
        internal static int ReadId(object row)
        {
            if (row == null) return int.MinValue;
            ColumnHandle h = Resolve(row.GetType(), "Id");
            if (h == null) return int.MinValue;
            object v = h.Get(row);
            if (v == null) return int.MinValue;
            try { return Convert.ToInt32(v, CultureInfo.InvariantCulture); }
            catch { return int.MinValue; }
        }

        /// <summary>
        /// Last-resort read of a value-type field straight out of the object's memory.
        ///
        /// Why this exists: for Turrets.ParentTurretId BOTH typed paths raise --
        /// the named getter goes through IL2CPP::il2cpp_runtime_invoke, and the
        /// "_ParentTurretId_k__BackingField" getter goes through
        /// Il2CppClassPointerStore&lt;Il2CppSystem.Nullable&lt;int&gt;&gt;::NativeClassPtr + il2cpp_value_box.
        /// Reading the field by offset bypasses both.
        ///
        /// Every offset comes from the runtime (IL2CPP.il2cpp_field_get_offset over the proxy's own
        /// NativeFieldInfoPtr_* statics), so nothing about the layout is assumed. The whole attempt
        /// is returned as a trace string so a single live run shows whether it worked, and callers
        /// cross-check it against the typed read whenever the typed read does succeed.
        /// </summary>
        internal static bool TryRawNullableIntRead(object row, ColumnHandle h, out object value, out string trace)
        {
            value = null;
            trace = null;
            try
            {
                if (h == null) { trace = "no handle"; return false; }
                Type t = row.GetType();
                string backingName = "_" + h.Column + "_k__BackingField";
                FieldInfo fi = t.GetField("NativeFieldInfoPtr_" + backingName,
                    BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Static);
                if (fi == null) { trace = "no NativeFieldInfoPtr_" + backingName; return false; }
                IntPtr fieldInfo = (IntPtr)fi.GetValue(null);
                if (fieldInfo == IntPtr.Zero) { trace = "NativeFieldInfoPtr_" + backingName + " is zero"; return false; }

                Type payload = Il2CppNullable.UnderlyingOf(h.ValueType);
                if (payload == null) { trace = "declared type " + h.ValueType + " is not Il2CppSystem.Nullable<T>"; return false; }
                if (payload != typeof(int)) { trace = "raw read only implemented for Nullable<int>, got Nullable<" + payload.Name + ">"; return false; }

                var box = row as Il2CppObjectBase;
                if (box == null) { trace = "row is not an Il2CppObjectBase"; return false; }
                IntPtr objPtr = box.Pointer;
                if (objPtr == IntPtr.Zero) { trace = "row pointer is zero"; return false; }

                uint structOffset = IL2CPP.il2cpp_field_get_offset(fieldInfo);

                Type nullableType = h.ValueType;
                IntPtr hasField = StaticFieldInfoPtr(nullableType, "NativeFieldInfoPtr_hasValue");
                IntPtr valField = StaticFieldInfoPtr(nullableType, "NativeFieldInfoPtr_value");
                if (hasField == IntPtr.Zero || valField == IntPtr.Zero)
                {
                    trace = "Nullable<int> inner field infos unavailable (has=" + hasField + " val=" + valField + ")";
                    return false;
                }
                uint hasOffset = IL2CPP.il2cpp_field_get_offset(hasField);
                uint valOffset = IL2CPP.il2cpp_field_get_offset(valField);

                IntPtr at = IntPtr.Add(objPtr, checked((int)structOffset));
                byte hasByte = Marshal.ReadByte(at, checked((int)hasOffset));
                int raw = Marshal.ReadInt32(at, checked((int)valOffset));
                bool hasValue = hasByte != 0;

                trace = "structOff=" + structOffset + " hasOff=" + hasOffset + " valOff=" + valOffset
                        + " hasByte=" + hasByte + " rawInt=" + raw;
                if (hasValue) value = raw;
                return true;
            }
            catch (Exception ex)
            {
                trace = "threw " + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        private static IntPtr StaticFieldInfoPtr(Type proxyType, string fieldName)
        {
            try
            {
                FieldInfo f = proxyType.GetField(fieldName, BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Static);
                if (f == null) return IntPtr.Zero;
                object v = f.GetValue(null);
                return v is IntPtr ? (IntPtr)v : IntPtr.Zero;
            }
            catch { return IntPtr.Zero; }
        }

        /// <summary>
        /// Describe the outcome of EVERY read path for one column, so a single live run answers
        /// "which path works and what does the failing one say" without another launch.
        /// </summary>
        internal static string Diagnose(object row, string column)
        {
            var sb = new StringBuilder();
            Type rowType = row == null ? null : row.GetType();
            ColumnHandle h = row == null ? null : Resolve(rowType, column);

            sb.Append(rowType == null ? "(null row)" : rowType.FullName).Append('.').Append(column);
            sb.Append(" | declared=").Append(h == null || h.ValueType == null ? "(unresolved)" : h.ValueType.FullName);
            sb.Append(" kind=").Append(h == null ? "-" : h.Kind.ToString());
            sb.Append(" backingProp=").Append(h != null && h.BackingProp != null ? h.BackingProp.Name : "(none)");
            sb.Append(" field=").Append(h != null && h.Field != null ? h.Field.Name : "(none)");
            sb.Append(" | ");
            RecordPath(sb, "path1-named", row, h == null ? null : h.Prop, null);
            sb.Append(" | ");
            RecordPath(sb, "path2-backing", row, h == null ? null : h.BackingProp, null);
            sb.Append(" | ");
            RecordPath(sb, "path3-field", row, null, h == null ? null : h.Field);
            sb.Append(" | path4-raw=");
            object rawValue;
            string rawTrace;
            if (TryRawNullableIntRead(row, h, out rawValue, out rawTrace))
                sb.Append("OK value=").Append(rawValue == null ? "null" : rawValue.ToString()).Append(" (").Append(rawTrace).Append(')');
            else
                sb.Append("FAILED ").Append(rawTrace);
            return sb.ToString();
        }

        private static void RecordPath(StringBuilder sb, string label, object row, PropertyInfo p, FieldInfo f)
        {
            sb.Append(label).Append('=');
            if (p == null && f == null) { sb.Append("ABSENT"); return; }
            try
            {
                object v = p != null ? p.GetValue(row) : f.GetValue(row);
                if (v == null) { sb.Append("OK null"); return; }
                sb.Append("OK ").Append(v.GetType().FullName);
                if (Il2CppNullable.IsProxyNullable(v.GetType()))
                {
                    object inner;
                    bool ok = Il2CppNullable.TryUnwrap(v, out inner);
                    sb.Append(" unwrap=").Append(ok ? (inner == null ? "hasValue=false" : "value=" + inner) : "REFUSED");
                }
                else { sb.Append(" value=").Append(v); }
            }
            catch (Exception ex)
            {
                sb.Append("THREW ").Append(ex.GetType().FullName).Append(": ").Append(ex.Message);
                Exception ie = ex.InnerException;
                for (int d = 0; ie != null && d < 3; d++)
                {
                    sb.Append(" <- ").Append(ie.GetType().FullName).Append(": ").Append(ie.Message);
                    ie = ie.InnerException;
                }
            }
        }

        /// <summary>Read a model reference as its Id, or null when the reference is dead.</summary>
        internal static object ReadReferenceId(object value)
        {
            if (IsNull(value)) return null;
            ColumnHandle id = Resolve(value.GetType(), "Id");
            if (id == null) return null;
            object v = id.Get(value);
            if (v == null) return null;
            try { return Convert.ToInt64(v, CultureInfo.InvariantCulture); }
            catch { return null; }
        }
    }

    /// <summary>
    /// Write-ahead journal, modelled on BARO's proven primitive:
    ///
    ///     private static void Set(object row, PropertyInfo p, object value)
    ///     {
    ///         IntPtr pointer = ((Il2CppObjectBase)row).Pointer;
    ///         ...
    ///         Orig = p.GetValue(row) ...  p.SetValue(row, value);
    ///     }
    ///
    /// The original value is captured BEFORE the write, so Restore() can put everything back
    /// in reverse order. Nothing here invents a new mechanism.
    /// </summary>
    internal static class WriteJournal
    {
        private sealed class Entry
        {
            internal object Row;
            internal ColumnHandle Handle;
            internal object Original;
            internal string Table;
            internal int Id;
            internal string Column;
            internal string NewValueText;
        }

        private static readonly List<Entry> Entries = new List<Entry>();
        private static readonly object Gate = new object();

        internal static int Count { get { lock (Gate) { return Entries.Count; } } }

        /// <summary>Capture the pre-write value, then write. Returns the original value.</summary>
        internal static object WriteAndRecord(object row, ColumnHandle handle, object value, string table, int id)
        {
            object original = handle.Get(row);
            handle.Set(row, value);
            var e = new Entry
            {
                Row = row,
                Handle = handle,
                Original = original,
                Table = table,
                Id = id,
                Column = handle.Column,
                NewValueText = Convert.ToString(value, CultureInfo.InvariantCulture)
            };
            lock (Gate) { Entries.Add(e); }
            return original;
        }

        /// <summary>Put every recorded cell back to the value it had before we touched it.</summary>
        internal static int Restore()
        {
            List<Entry> snapshot;
            lock (Gate)
            {
                if (Entries.Count == 0) return 0;
                snapshot = new List<Entry>(Entries);
                Entries.Clear();
            }
            int restored = 0, failed = 0;
            for (int i = snapshot.Count - 1; i >= 0; i--)
            {
                Entry e = snapshot[i];
                try { e.Handle.Set(e.Row, e.Original); restored++; }
                catch (Exception ex)
                {
                    failed++;
                    ProbeLog.Error("Restore failed for " + e.Table + "[" + e.Id + "]." + e.Column, ex);
                }
            }
            ProbeLog.Info("Restore: restored=" + restored + " failed=" + failed + " (journal was " + snapshot.Count + " cells)");
            return restored;
        }

        /// <summary>
        /// Forget the journal WITHOUT writing anything back.
        ///
        /// Used when a database (re)load is observed. Load() rebuilds the source object, so the
        /// values this probe wrote are already gone, and the row proxies in the journal may have
        /// been freed. Writing through a freed Il2Cpp object is a NATIVE access violation, not a
        /// managed exception, so the reload path deliberately does not touch them. Restore() stays
        /// for the live-database paths (the restore.flag control file, and override.json being
        /// edited mid-session).
        /// </summary>
        internal static int Discard()
        {
            lock (Gate)
            {
                int n = Entries.Count;
                Entries.Clear();
                return n;
            }
        }

        internal static List<string> Describe(int limit)
        {
            var lines = new List<string>();
            lock (Gate)
            {
                int n = Math.Min(limit, Entries.Count);
                for (int i = 0; i < n; i++)
                {
                    Entry e = Entries[i];
                    lines.Add(e.Table + "[" + e.Id + "]." + e.Column + " := " + e.NewValueText);
                }
                if (Entries.Count > n) lines.Add("... +" + (Entries.Count - n) + " more");
            }
            return lines;
        }
    }
}
