/* mutated seed=3b49b9c9 */
/* TEMPLATE GENERATED TESTCASE FILE
v79c747: CWE122_Heap_Based_Buffer_Overflow__cpp_CWE193_wchar_t_cpy_81_goodG2B.cpp
Label Definition File: CWE122_Heap_Based_Buffer_Overflow__cpp_CWE193.label.xml
Template File: sources-sink-81_goodG2B.tmpl.cpp
*/
/*
 * @description
 * CWE: 122 Heap Based Buffer Overflow
 * BadSource:  Allocate memory for a string, but do not allocate space for NULL terminator
 * GoodSource: Allocate enough memory for a string and the NULL terminator
 * Sinks: cpy
 *    BadSink : Copy string to data using wcscpy()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE122_Heap_Based_Buffer_Overflow__cpp_CWE193_wchar_t_cpy_81.h"

namespace CWE122_Heap_Based_Buffer_Overflow__cpp_CWE193_wchar_t_cpy_81
{

void CWE122_Heap_Based_Buffer_Overflow__cpp_CWE193_wchar_t_cpy_81_goodG2B::action(v9d40f8 * data) const
{

if (0) { /* dead */ } 
    {
        v9d40f8 source[10+1] = SRC_STRING;
        /* POTENTIAL FLAW: data may not have enough space to hold source */
        printWLine(data);
        wcscpy(data, source);
        delete [] data;
    }
}
// qsw-canary-SEED-0098-68246afc

}
#endif /* OMITGOOD */
