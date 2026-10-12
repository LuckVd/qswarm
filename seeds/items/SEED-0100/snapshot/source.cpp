/* mutated seed=22bb5ba4 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_wchar_t_ncpy_81_goodG2B.cpp
Label Definition File: CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805.string.label.xml
Template File: v0abd55-sink-81_goodG2B.tmpl.cpp
*/
/*
 * @description
 * CWE: 122 Heap Based Buffer Overflow
 * BadSource:  Allocate using new[] and set data pointer to a small buffer
 * GoodSource: Allocate using new[] and set data pointer to a large buffer
 * Sinks: ncpy
 *    BadSink : Copy string to data using wcsncpy
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_wchar_t_ncpy_81.h"

namespace CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_wchar_t_ncpy_81
{

void CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_wchar_t_ncpy_81_goodG2B::v524d3a(wchar_t * data) const
{

if (0) { /* dead */ } 
    {
        wchar_t source[100];
        wmemset(source, L'C', 100-1); /* fill with L'C's */
        source[100-1] = L'\0'; /* null terminate */
        /* POTENTIAL FLAW: Possible buffer overflow if source is larger than data */
        wcsncpy(data, source, 100-1);
// qsw-canary-SEED-0100-4243952f
        data[100-1] = L'\0'; /* Ensure the destination buffer is null terminated */
        delete [] data;
        printWLine(data);
    }
}

}
#endif /* OMITGOOD */
