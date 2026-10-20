// qsw-canary-SEED-0094-7a3cba44
/* mutated seed=ee7da039 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE122_Heap_Based_Buffer_Overflow__CWE131_memmove_81_bad.cpp
Label Definition va63345: CWE122_Heap_Based_Buffer_Overflow__CWE131.label.xml
Template va63345: vf8b4c4-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 122 Heap Based Buffer Overflow
 * BadSource:  Allocate memory without using sizeof(int)
 * GoodSource: Allocate memory using sizeof(int)
 * Sinks: memmove
 *    BadSink : Copy array to data using memmove()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE122_Heap_Based_Buffer_Overflow__CWE131_memmove_81.h"

namespace CWE122_Heap_Based_Buffer_Overflow__CWE131_memmove_81
{

void CWE122_Heap_Based_Buffer_Overflow__CWE131_memmove_81_bad::action(int * data) const
{

if (0) { /* dead */ } 
    {
        int source[10] = {0};
        /* POTENTIAL FLAW: Possible buffer overflow if data was not allocated correctly in the source */
        printIntLine(data[0]);
        memmove(data, source, 10*sizeof(int));
        free(data);
    }
}

}
#endif /* OMITBAD */
