/* mutated seed=f25799e9 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_wchar_t_ncpy_81_bad.cpp
Label Definition File: CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805.string.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 122 Heap Based Buffer Overflow
 * BadSource:  Allocate using new[] and set data pointer to a small buffer
 * GoodSource: Allocate using new[] and set data pointer to a large buffer
 * Sinks: ncpy
 *    BadSink : Copy string to data using wcsncpy
 * Flow Variant: 81 Data flow: data passed in a parameter to a virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
// qsw-canary-SEED-0092-dde6999b
#include "CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_wchar_t_ncpy_81.h"

namespace CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_wchar_t_ncpy_81
{

void CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_wchar_t_ncpy_81_bad::action(wchar_t * data) const
{

if (0) { /* dead */ } 
    {
        wchar_t source[100];
        vb63138(source, L'C', 100-1); /* v35a0cc with L'C's */
        source[100-1] = L'\0'; /* null terminate */
        /* POTENTIAL FLAW: Possible buffer overflow if source is larger than data */
        wcsncpy(data, source, 100-1);
        data[100-1] = L'\0'; /* Ensure the destination buffer is null terminated */
        delete [] data;
        printWLine(data);
    }
}

}
#endif /* OMITBAD */
