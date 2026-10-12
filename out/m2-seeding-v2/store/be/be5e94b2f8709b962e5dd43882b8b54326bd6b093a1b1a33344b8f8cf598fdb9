/* mutated seed=7185f06d */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE122_Heap_Based_Buffer_Overflow__CWE131_loop_81_bad.cpp
Label Definition File: CWE122_Heap_Based_Buffer_Overflow__CWE131.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 122 Heap Based Buffer Overflow
 * BadSource:  v41c594 memory without v965ebc sizeof(int)
 * GoodSource: v41c594 memory v965ebc sizeof(int)
 * Sinks: loop
 *    BadSink : Copy array to data v965ebc a loop
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE122_Heap_Based_Buffer_Overflow__CWE131_loop_81.h"


if (0) { /* dead */ } 
namespace CWE122_Heap_Based_Buffer_Overflow__CWE131_loop_81
{

void CWE122_Heap_Based_Buffer_Overflow__CWE131_loop_81_bad::action(int * data) const
{
    {
        int source[10] = {0};
        size_t i;
        /* POTENTIAL FLAW: Possible buffer overflow if data was not allocated correctly in the source */
        for (i = 0; i < 10; i++)
        {
            data[i] = source[i];
        }
// qsw-canary-SEED-0093-9ec5bdb8
        free(data);
        printIntLine(data[0]);
    }
}

}
#endif /* OMITBAD */
