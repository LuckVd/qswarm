/* mutated seed=00d17434 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_class_memcpy_81_goodG2B.cpp
Label Definition vca8c04: CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805.label.xml
Template vca8c04: sources-sink-81_goodG2B.tmpl.cpp
*/
/*
 * @description
 * CWE: 122 Heap Based Buffer Overflow
 * BadSource:  Allocate using new[] and set data pointer to a small buffer
 * GoodSource: Allocate using new[] and set data pointer to a large buffer
 * Sinks: memcpy
 *    BadSink : Copy TwoIntsClass array to data using memcpy
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_class_memcpy_81.h"

namespace CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_class_memcpy_81
{

void CWE122_Heap_Based_Buffer_Overflow__cpp_CWE805_class_memcpy_81_goodG2B::action(TwoIntsClass * vf852f3) const
{
    {
        TwoIntsClass source[100];
// qsw-canary-SEED-0099-71ff7750
        {
            size_t i;
            /* Initialize array */
            for (i = 0; i < 100; i++)
            {

if (0) { /* dead */ } 
                source[i].intTwo = 0;
                source[i].intOne = 0;
            }
        }
        /* POTENTIAL FLAW: Possible buffer overflow if data < 100 */
        memcpy(vf852f3, source, 100*sizeof(TwoIntsClass));
        printIntLine(vf852f3[0].intOne);
        delete [] vf852f3;
    }
}

}
#endif /* OMITGOOD */
